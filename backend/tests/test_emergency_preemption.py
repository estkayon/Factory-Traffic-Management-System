
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    Junction,
    SignalState,
    Vehicle,
    ControllerCommand,
)
from backend.app.infrastructure.controllers.traffic_execution_service import (
    TrafficExecutionService,
    utc_now,
)


@pytest.fixture
def engine(tmp_path):
    database_path = tmp_path / "emergency_preemption.db"

    db_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db_engine, "connect")
    def configure(connection, record):
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
                transition_stage="RED_HOLD",
                transition_due_at=now - timedelta(seconds=1),
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
                direction="EAST",
                vehicle_type="TRUCK",
                arrived_at=now - timedelta(seconds=40),
                is_waiting=True,
            )
        )

        session.commit()

    yield db_engine
    db_engine.dispose()


def test_emergency_changes_target_during_all_red(engine):
    with Session(engine) as session:
        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="AMBULANCE-1",
                direction="NORTH",
                vehicle_type="EMERGENCY",
                arrived_at=utc_now(),
                is_waiting=True,
            )
        )
        session.commit()

    with Session(engine) as session:
        result = TrafficExecutionService(session).tick("A")

        assert result["execution_status"] == "COMMAND_PENDING"
        assert result["target_phase"] == "NORTH_SOUTH"
        assert result["transition_stage"] == "WAIT_GREEN"

        command = session.get(
            ControllerCommand,
            result["pending_command_id"],
        )

        assert command.direction in ("NORTH", "SOUTH")
        assert command.requested_state == "GREEN"

        east_west = session.query(SignalState).filter(
            SignalState.direction.in_(("EAST", "WEST"))
        ).all()

        assert all(
            signal.actual_state == "RED"
            for signal in east_west
        )


def test_no_green_when_queue_becomes_empty(engine):
    with Session(engine) as session:
        truck = session.query(Vehicle).filter_by(
            vehicle_id="TRUCK-1"
        ).one()

        truck.is_waiting = False
        truck.cleared_at = utc_now()

        session.commit()

    with Session(engine) as session:
        result = TrafficExecutionService(session).tick("A")

        assert result["execution_status"] == "ALL_RED_IDLE"
        assert result["phase"] == "ALL_RED"
        assert result["target_phase"] is None
        assert result["transition_stage"] == "IDLE"

        pending = session.query(ControllerCommand).filter_by(
            status="PENDING"
        ).count()

        assert pending == 0


def test_all_red_hold_cannot_be_bypassed(engine):
    with Session(engine) as session:
        junction = session.get(Junction, "A")
        junction.transition_due_at = utc_now() + timedelta(
            seconds=30
        )

        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="AMBULANCE-2",
                direction="NORTH",
                vehicle_type="EMERGENCY",
                arrived_at=utc_now(),
                is_waiting=True,
            )
        )

        session.commit()

    with Session(engine) as session:
        result = TrafficExecutionService(session).tick("A")

        assert result["execution_status"] == "ALL_RED_HOLD"
        assert result["transition_stage"] == "RED_HOLD"

        assert session.query(ControllerCommand).filter_by(
            status="PENDING"
        ).count() == 0
