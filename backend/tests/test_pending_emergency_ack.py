
"""Integration tests for emergency arrival during pending GREEN commands."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.controllers.command_service import (
    ControllerCommandService,
)
from backend.app.infrastructure.controllers.traffic_execution_service import (
    TrafficExecutionService,
    utc_now,
)
from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    ControllerCommand,
    Junction,
    SignalState,
    Vehicle,
)


DIRECTIONS = ("NORTH", "SOUTH", "EAST", "WEST")


@pytest.fixture
def engine(tmp_path):
    """Create a junction during partial EAST/WEST activation."""
    db_path = tmp_path / "pending_emergency.db"

    db_engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db_engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(db_engine)

    now = utc_now()

    with Session(db_engine) as session:
        session.add(
            Junction(
                id="A",
                name="Test Junction",
                mode="AUTOMATIC",
                active_phase="ALL_RED",
                target_phase="EAST_WEST",
                transition_stage="WAIT_GREEN",
                controller_status="ONLINE",
            )
        )
        session.flush()

        for direction in DIRECTIONS:
            color = "GREEN" if direction == "EAST" else "RED"

            session.add(
                SignalState(
                    junction_id="A",
                    direction=direction,
                    desired_state=(
                        "GREEN" if direction == "WEST" else color
                    ),
                    actual_state=color,
                )
            )

        pending = ControllerCommand(
            junction_id="A",
            direction="WEST",
            requested_state="GREEN",
            status="PENDING",
            created_at=now,
            expires_at=now + timedelta(seconds=60),
        )
        session.add(pending)

        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="AMB-NORTH-01",
                direction="NORTH",
                vehicle_type="EMERGENCY",
                arrived_at=now,
                is_waiting=True,
            )
        )

        session.commit()
        command_id = pending.command_id

    try:
        yield db_engine, command_id
    finally:
        db_engine.dispose()


def tick(engine):
    """Run one controller execution cycle."""
    with Session(engine) as session:
        return TrafficExecutionService(session).tick("A")


def ack(engine, command_id, color):
    """Confirm a controller command and return plain status."""
    with Session(engine) as session:
        command = ControllerCommandService(session).acknowledge(
            command_id=command_id,
            junction_id="A",
            status="ACK",
            actual_state=color,
        )
        return command.status


def get_signals(engine):
    """Read controller-confirmed signal states."""
    with Session(engine) as session:
        rows = session.scalars(select(SignalState)).all()
        return {row.direction: row.actual_state for row in rows}


def assert_no_conflicting_green(engine):
    """Opposing traffic phases must never be GREEN together."""
    signals = get_signals(engine)

    north_south = any(
        signals[d] == "GREEN" for d in ("NORTH", "SOUTH")
    )
    east_west = any(
        signals[d] == "GREEN" for d in ("EAST", "WEST")
    )

    assert not (north_south and east_west)


def expire_hold(engine):
    """Advance the test's persisted hold deadline."""
    with Session(engine) as session:
        junction = session.get(Junction, "A")
        junction.transition_due_at = utc_now() - timedelta(seconds=1)
        session.commit()


def test_emergency_waits_for_pending_green_ack(engine):
    db_engine, original_command_id = engine

    result = tick(db_engine)

    assert result["execution_status"] == "WAITING_FOR_ACK"
    assert result["pending_command_id"] == original_command_id
    assert result["transition_stage"] == "WAIT_GREEN"

    # No new controller command may be issued.
    with Session(db_engine) as session:
        commands = session.scalars(
            select(ControllerCommand)
        ).all()

        assert len(commands) == 1
        assert commands[0].status == "PENDING"

    assert_no_conflicting_green(db_engine)


def test_emergency_safely_interrupts_after_ack(engine):
    db_engine, original_command_id = engine

    # Resolve the old WEST GREEN command.
    assert ack(db_engine, original_command_id, "GREEN") == "ACKED"

    # Both EAST and WEST now need safe clearance.
    first = tick(db_engine)

    assert first["transition_stage"] == "ABORT_WAIT_YELLOW"
    assert first["execution_status"] == "COMMAND_PENDING"
    assert_no_conflicting_green(db_engine)

    assert ack(
        db_engine, first["pending_command_id"], "YELLOW"
    ) == "ACKED"

    second = tick(db_engine)

    assert second["execution_status"] == "COMMAND_PENDING"

    assert ack(
        db_engine, second["pending_command_id"], "YELLOW"
    ) == "ACKED"

    hold = tick(db_engine)
    assert hold["execution_status"] == "ABORT_YELLOW_HOLD"

    expire_hold(db_engine)

    # Confirm RED on both previously GREEN directions.
    for _ in range(2):
        result = tick(db_engine)
        assert result["execution_status"] == "COMMAND_PENDING"

        assert ack(
            db_engine, result["pending_command_id"], "RED"
        ) == "ACKED"

        assert_no_conflicting_green(db_engine)

    cleared = tick(db_engine)

    assert cleared["execution_status"] == "ALL_RED_HOLD"
    assert all(
        color == "RED"
        for color in get_signals(db_engine).values()
    )

    expire_hold(db_engine)

    emergency = tick(db_engine)

    assert emergency["target_phase"] == "NORTH_SOUTH"
    assert emergency["transition_stage"] == "WAIT_GREEN"
    assert emergency["execution_status"] == "COMMAND_PENDING"

    # A GREEN command is allowed only after confirmed ALL_RED.
    assert get_signals(db_engine)["EAST"] == "RED"
    assert get_signals(db_engine)["WEST"] == "RED"

    assert_no_conflicting_green(db_engine)


def test_pending_green_timeout_blocks_emergency_switch(engine):
    db_engine, command_id = engine

    # Simulate the controller failing to acknowledge WEST.
    with Session(db_engine) as session:
        command = session.get(ControllerCommand, command_id)
        command.expires_at = utc_now() - timedelta(seconds=1)
        session.commit()

    result = tick(db_engine)

    assert result["execution_status"] == "COMMAND_TIMEOUT"
    assert result["controller_status"] == "DEGRADED"

    next_result = tick(db_engine)

    assert next_result["execution_status"] == "CONTROLLER_NOT_READY"
    assert next_result["pending_command_id"] is None

    with Session(db_engine) as session:
        commands = session.scalars(
            select(ControllerCommand)
        ).all()

        assert len(commands) == 1
        assert commands[0].status == "TIMEOUT"

    assert get_signals(db_engine)["WEST"] == "UNKNOWN"
    assert_no_conflicting_green(db_engine)
