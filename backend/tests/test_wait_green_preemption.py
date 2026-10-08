
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    Junction,
    SignalState,
    Vehicle,
)
from backend.app.infrastructure.controllers.command_service import (
    ControllerCommandService,
)
from backend.app.infrastructure.controllers.traffic_execution_service import (
    TrafficExecutionService,
    utc_now,
)


@pytest.fixture
def engine(tmp_path):
    database = tmp_path / "wait_green_emergency.db"

    db_engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db_engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(db_engine)

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

        # EAST is confirmed GREEN, WEST is still RED.
        for direction in ("NORTH", "SOUTH", "EAST", "WEST"):
            color = "GREEN" if direction == "EAST" else "RED"

            session.add(
                SignalState(
                    junction_id="A",
                    direction=direction,
                    desired_state=color,
                    actual_state=color,
                )
            )

        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="EMERGENCY-N1",
                direction="NORTH",
                vehicle_type="EMERGENCY",
                arrived_at=utc_now(),
                is_waiting=True,
            )
        )

        session.commit()

    yield db_engine
    db_engine.dispose()


def tick(engine):
    with Session(engine) as session:
        return TrafficExecutionService(session).tick("A")


def ack(engine, command_id, color):
    with Session(engine) as session:
        command = ControllerCommandService(session).acknowledge(
            command_id, "A", "ACK", color
        )
        return command.status


def expire_hold(engine):
    with Session(engine) as session:
        junction = session.get(Junction, "A")
        junction.transition_due_at = utc_now() - timedelta(seconds=1)
        session.commit()


def signal_states(engine):
    with Session(engine) as session:
        return {
            signal.direction: signal.actual_state
            for signal in session.query(SignalState).all()
        }


def test_partial_green_is_safely_aborted(engine):
    first = tick(engine)

    assert first["transition_stage"] == "ABORT_WAIT_YELLOW"
    assert first["execution_status"] == "COMMAND_PENDING"

    assert ack(engine, first["pending_command_id"], "YELLOW") == "ACKED"

    hold = tick(engine)

    assert hold["execution_status"] == "ABORT_YELLOW_HOLD"

    colors = signal_states(engine)
    assert colors["EAST"] == "YELLOW"
    assert colors["WEST"] == "RED"

    expire_hold(engine)

    red_command = tick(engine)

    assert red_command["transition_stage"] == "ABORT_WAIT_RED"
    assert ack(
        engine, red_command["pending_command_id"], "RED"
    ) == "ACKED"

    cleared = tick(engine)

    assert cleared["execution_status"] == "ALL_RED_HOLD"
    assert all(
        color == "RED"
        for color in signal_states(engine).values()
    )


def test_emergency_cannot_skip_clearance(engine):
    first = tick(engine)
    ack(engine, first["pending_command_id"], "YELLOW")
    tick(engine)

    # YELLOW hold has not expired.
    result = tick(engine)

    assert result["execution_status"] == "ABORT_YELLOW_HOLD"
    assert result["pending_command_id"] is None

    colors = signal_states(engine)
    assert colors["NORTH"] == "RED"
    assert colors["SOUTH"] == "RED"


def test_emergency_target_selected_after_abort(engine):
    first = tick(engine)
    ack(engine, first["pending_command_id"], "YELLOW")
    tick(engine)

    expire_hold(engine)

    red = tick(engine)
    ack(engine, red["pending_command_id"], "RED")

    tick(engine)  # Enter RED_HOLD
    expire_hold(engine)

    result = tick(engine)

    assert result["execution_status"] == "COMMAND_PENDING"
    assert result["target_phase"] == "NORTH_SOUTH"
    assert result["transition_stage"] == "WAIT_GREEN"

    colors = signal_states(engine)
    assert colors["EAST"] == "RED"
    assert colors["WEST"] == "RED"
