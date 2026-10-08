
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
    """Create an isolated junction with NORTH/SOUTH active."""

    database_path = tmp_path / "phase_switching.db"

    db_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
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
                name="Factory Junction A",
                mode="AUTOMATIC",
                active_phase="NORTH_SOUTH",
                transition_stage="IDLE",
                controller_status="ONLINE",
                phase_started_at=now - timedelta(seconds=40),
            )
        )
        session.flush()

        for direction in DIRECTIONS:
            color = (
                "GREEN"
                if direction in ("NORTH", "SOUTH")
                else "RED"
            )

            session.add(
                SignalState(
                    junction_id="A",
                    direction=direction,
                    desired_state=color,
                    actual_state=color,
                    confirmed_at=now,
                )
            )

        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="TRUCK-EAST-001",
                direction="EAST",
                vehicle_type="TRUCK",
                arrived_at=now - timedelta(seconds=40),
                is_waiting=True,
            )
        )

        session.commit()

    try:
        yield db_engine
    finally:
        db_engine.dispose()


def tick(engine):
    """Execute one persistent control cycle."""

    with Session(engine) as session:
        return TrafficExecutionService(session).tick("A")



def acknowledge(engine, command_id, color):
    """Acknowledge a controller command and return its status."""

    with Session(engine) as session:
        command = ControllerCommandService(session).acknowledge(
            command_id=command_id,
            junction_id="A",
            status="ACK",
            actual_state=color,
        )

        return command.status



def signals(engine):
    """Read controller-confirmed signal states."""

    with Session(engine) as session:
        rows = session.scalars(select(SignalState)).all()

        return {
            row.direction: row.actual_state
            for row in rows
        }


def expire_hold(engine):
    """Advance a persisted hold deadline for deterministic testing."""

    with Session(engine) as session:
        junction = session.get(Junction, "A")

        assert junction.transition_stage in {
            "YELLOW_HOLD",
            "RED_HOLD",
        }

        junction.transition_due_at = (
            utc_now() - timedelta(seconds=1)
        )
        session.commit()


def assert_no_conflicting_green(engine):
    """Verify the central traffic safety invariant."""

    current = signals(engine)

    north_south_green = any(
        current[direction] == "GREEN"
        for direction in ("NORTH", "SOUTH")
    )

    east_west_green = any(
        current[direction] == "GREEN"
        for direction in ("EAST", "WEST")
    )

    assert not (
        north_south_green and east_west_green
    ), "Conflicting traffic phases must never be GREEN together."


def acknowledge_pending(engine, result, expected_color):
    """Confirm one pending command and verify safety."""

    assert result["execution_status"] == "COMMAND_PENDING"

    command_id = result["pending_command_id"]

    with Session(engine) as session:
        command = session.get(ControllerCommand, command_id)

        assert command is not None
        assert command.requested_state == expected_color

    command_status = acknowledge(engine, command_id, expected_color)

    assert command_status == "ACKED"

    assert_no_conflicting_green(engine)


def complete_safe_switch(engine):
    """Execute the complete NORTH/SOUTH -> EAST/WEST sequence."""

    # 1. Begin YELLOW transition.
    first = tick(engine)

    assert first["transition_stage"] == "WAIT_YELLOW"
    acknowledge_pending(engine, first, "YELLOW")

    # 2. Confirm the second YELLOW signal.
    second = tick(engine)
    acknowledge_pending(engine, second, "YELLOW")

    assert signals(engine)["NORTH"] == "YELLOW"
    assert signals(engine)["SOUTH"] == "YELLOW"

    # 3. Begin the five-second YELLOW hold.
    hold = tick(engine)

    assert hold["execution_status"] == "YELLOW_HOLD"
    assert hold["transition_stage"] == "YELLOW_HOLD"

    # 4. Expire the hold deadline.
    expire_hold(engine)

    # 5. Confirm RED for both directions.
    first_red = tick(engine)
    acknowledge_pending(engine, first_red, "RED")

    second_red = tick(engine)
    acknowledge_pending(engine, second_red, "RED")

    # 6. Enter ALL_RED clearance.
    all_red = tick(engine)

    assert all_red["execution_status"] == "ALL_RED_HOLD"
    assert all(
        state == "RED"
        for state in signals(engine).values()
    )

    # 7. Expire two-second clearance period.
    expire_hold(engine)

    # 8. Confirm GREEN for EAST and WEST.
    first_green = tick(engine)
    acknowledge_pending(engine, first_green, "GREEN")

    second_green = tick(engine)
    acknowledge_pending(engine, second_green, "GREEN")

    # 9. Complete the phase transition.
    final = tick(engine)

    assert_no_conflicting_green(engine)

    return final


def test_complete_conflicting_phase_switch(engine):
    result = complete_safe_switch(engine)

    assert result["execution_status"] == "PHASE_ACTIVE"
    assert result["phase"] == "EAST_WEST"
    assert result["transition_stage"] == "IDLE"

    assert signals(engine) == {
        "NORTH": "RED",
        "SOUTH": "RED",
        "EAST": "GREEN",
        "WEST": "GREEN",
    }


def test_yellow_hold_does_not_end_early(engine):
    first = tick(engine)
    acknowledge_pending(engine, first, "YELLOW")

    second = tick(engine)
    acknowledge_pending(engine, second, "YELLOW")

    tick(engine)  # Enter YELLOW_HOLD

    result = tick(engine)

    assert result["execution_status"] == "YELLOW_HOLD"
    assert result["transition_stage"] == "YELLOW_HOLD"

    assert signals(engine)["EAST"] == "RED"
    assert signals(engine)["WEST"] == "RED"


def test_all_red_clearance_does_not_end_early(engine):
    first = tick(engine)
    acknowledge_pending(engine, first, "YELLOW")

    second = tick(engine)
    acknowledge_pending(engine, second, "YELLOW")

    tick(engine)
    expire_hold(engine)

    first_red = tick(engine)
    acknowledge_pending(engine, first_red, "RED")

    second_red = tick(engine)
    acknowledge_pending(engine, second_red, "RED")

    tick(engine)  # Enter RED_HOLD

    result = tick(engine)

    assert result["execution_status"] == "ALL_RED_HOLD"
    assert all(
        state == "RED"
        for state in signals(engine).values()
    )


def test_controller_failure_prevents_phase_switch(engine):
    first = tick(engine)

    assert first["execution_status"] == "COMMAND_PENDING"

    with Session(engine) as session:
        command = session.get(
            ControllerCommand,
            first["pending_command_id"],
        )

        command.expires_at = (
            utc_now() - timedelta(seconds=1)
        )
        session.commit()

    result = tick(engine)

    assert result["execution_status"] == "COMMAND_TIMEOUT"
    assert result["controller_status"] == "DEGRADED"

    next_result = tick(engine)

    assert next_result["execution_status"] == "CONTROLLER_NOT_READY"

    assert_no_conflicting_green(engine)
