
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.domain.services.manual_control_service import (
    ManualControlError,
    ManualControlService,
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
    path = tmp_path / "manual_control.db"

    db_engine = create_engine(
        f"sqlite:///{path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db_engine, "connect")
    def configure(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(db_engine)

    with Session(db_engine) as session:
        session.add(
            Junction(
                id="A",
                name="Test Junction",
                mode="AUTOMATIC",
                active_phase="NORTH_SOUTH",
                controller_status="ONLINE",
                phase_started_at=utc_now() - timedelta(seconds=5),
            )
        )
        session.flush()

        for direction in ("NORTH", "SOUTH", "EAST", "WEST"):
            state = (
                "GREEN"
                if direction in ("NORTH", "SOUTH")
                else "RED"
            )
            session.add(
                SignalState(
                    junction_id="A",
                    direction=direction,
                    desired_state=state,
                    actual_state=state,
                )
            )

        session.commit()

    yield db_engine
    db_engine.dispose()


def manual(engine, command, direction=None):
    with Session(engine) as session:
        return ManualControlService(session).execute(
            "A", command, direction
        )


def tick(engine):
    with Session(engine) as session:
        return TrafficExecutionService(session).tick("A")


def add_emergency(engine, direction="EAST"):
    with Session(engine) as session:
        session.add(
            Vehicle(
                junction_id="A",
                vehicle_id="AMB-001",
                direction=direction,
                vehicle_type="EMERGENCY",
                arrived_at=utc_now(),
                is_waiting=True,
            )
        )
        session.commit()


def test_manual_request_is_persisted(engine):
    result = manual(engine, "MANUAL_GREEN_REQUEST", "WEST")

    assert result["mode"] == "MANUAL"
    assert result["manual_phase"] == "EAST_WEST"

    with Session(engine) as session:
        junction = session.get(Junction, "A")
        assert junction.manual_phase == "EAST_WEST"


def test_manual_request_starts_safe_yellow_transition(engine):
    manual(engine, "MANUAL_GREEN_REQUEST", "WEST")

    result = tick(engine)

    assert result["transition_stage"] == "WAIT_YELLOW"
    assert result["execution_status"] == "COMMAND_PENDING"
    assert result["target_phase"] == "EAST_WEST"


def test_return_to_automatic_clears_preference(engine):
    manual(engine, "MANUAL_GREEN_REQUEST", "WEST")

    result = manual(engine, "RETURN_TO_AUTOMATIC")

    assert result["mode"] == "AUTOMATIC"
    assert result["manual_phase"] is None


def test_emergency_has_priority_over_manual(engine):
    manual(engine, "MANUAL_GREEN_REQUEST", "NORTH")
    add_emergency(engine, "EAST")

    result = tick(engine)

    assert result["target_phase"] == "EAST_WEST"
    assert result["transition_stage"] == "WAIT_YELLOW"

    with Session(engine) as session:
        junction = session.get(Junction, "A")
        assert junction.mode == "EMERGENCY"
        assert junction.manual_phase == "NORTH_SOUTH"


def test_emergency_preserves_manual_preference(engine):
    manual(engine, "MANUAL_GREEN_REQUEST", "WEST")
    add_emergency(engine, "NORTH")

    result = manual(engine, "MANUAL_GREEN_REQUEST", "WEST")

    assert result["mode"] == "EMERGENCY"
    assert result["manual_phase"] == "EAST_WEST"


def test_invalid_manual_direction_is_rejected(engine):
    with Session(engine) as session:
        service = ManualControlService(session)

        with pytest.raises(ManualControlError):
            service.execute(
                "A", "MANUAL_GREEN_REQUEST", "INVALID"
            )

        session.rollback()
