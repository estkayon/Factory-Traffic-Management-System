
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event
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
    Junction,
    SignalState,
    Vehicle,
)


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "execution.db"

    db_engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db_engine, "connect")
    def configure_connection(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(db_engine)

    with Session(db_engine) as session:
        session.add(
            Junction(
                id="A",
                name="Test Junction",
                mode="AUTOMATIC",
                active_phase="ALL_RED",
                controller_status="ONLINE",
            )
        )
        session.flush()

        for direction in ("NORTH", "SOUTH", "EAST", "WEST"):
            session.add(
                SignalState(
                    junction_id="A",
                    direction=direction,
                    desired_state="RED",
                    actual_state="RED",
                )
            )

        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="TRUCK-1",
                direction="NORTH",
                vehicle_type="TRUCK",
                arrived_at=utc_now(),
                is_waiting=True,
            )
        )

        session.commit()

    yield db_engine
    db_engine.dispose()


def run_tick(engine):
    with Session(engine) as session:
        return TrafficExecutionService(session).tick("A")


def acknowledge(engine, command_id, color):
    with Session(engine) as session:
        return ControllerCommandService(session).acknowledge(
            command_id, "A", "ACK", color
        )


def test_initial_execution_starts_with_all_red(engine):
    result = run_tick(engine)

    assert result["execution_status"] == "ALL_RED_HOLD"
    assert result["phase"] == "ALL_RED"
    assert result["target_phase"] == "NORTH_SOUTH"


def test_initial_green_requires_two_acknowledgements(engine):
    run_tick(engine)

    # Simulate expiry of the ALL_RED clearance timer.
    with Session(engine) as session:
        junction = session.get(Junction, "A")
        junction.transition_due_at = utc_now() - timedelta(seconds=1)
        session.commit()

    first = run_tick(engine)

    assert first["execution_status"] == "COMMAND_PENDING"

    acknowledge(engine, first["pending_command_id"], "GREEN")

    second = run_tick(engine)

    assert second["execution_status"] == "COMMAND_PENDING"

    acknowledge(engine, second["pending_command_id"], "GREEN")

    final = run_tick(engine)

    assert final["execution_status"] == "PHASE_ACTIVE"
    assert final["phase"] == "NORTH_SOUTH"

    with Session(engine) as session:
        signals = {
            row.direction: row.actual_state
            for row in session.query(SignalState).all()
        }

    assert signals == {
        "NORTH": "GREEN",
        "SOUTH": "GREEN",
        "EAST": "RED",
        "WEST": "RED",
    }


def test_no_second_command_before_ack(engine):
    run_tick(engine)

    with Session(engine) as session:
        junction = session.get(Junction, "A")
        junction.transition_due_at = utc_now() - timedelta(seconds=1)
        session.commit()

    first = run_tick(engine)
    second = run_tick(engine)

    assert first["pending_command_id"] == second["pending_command_id"]
    assert second["execution_status"] == "WAITING_FOR_ACK"
