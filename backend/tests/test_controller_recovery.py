
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    ControllerCommand,
    Junction,
    SignalState,
)
from backend.app.infrastructure.controllers.recovery_service import (
    ControllerRecoveryService,
)


@pytest.fixture
def engine(tmp_path):
    database = tmp_path / "recovery_test.db"

    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            Junction(
                id="A",
                name="Test Junction",
                controller_status="ONLINE",
                active_phase="NORTH_SOUTH",
            )
        )
        session.flush()

        for direction in ("NORTH", "SOUTH", "EAST", "WEST"):
            session.add(
                SignalState(
                    junction_id="A",
                    direction=direction,
                    desired_state="GREEN"
                    if direction in ("NORTH", "SOUTH")
                    else "RED",
                    actual_state="GREEN"
                    if direction in ("NORTH", "SOUTH")
                    else "RED",
                )
            )

        session.commit()

    yield engine
    engine.dispose()


def test_restart_invalidates_actual_signal_states(engine):
    with Session(engine) as session:
        ControllerRecoveryService(session).recover_after_restart()

    with Session(engine) as session:
        junction = session.get(Junction, "A")
        signals = session.query(SignalState).all()

        assert junction.controller_status == "UNKNOWN"
        assert junction.active_phase == "ALL_RED"

        assert all(
            signal.actual_state == "UNKNOWN"
            for signal in signals
        )


def test_reconciliation_confirms_all_red(engine):
    with Session(engine) as session:
        ControllerRecoveryService(session).recover_after_restart()

    with Session(engine) as session:
        ControllerRecoveryService(session).reconcile_all_red("A")

    with Session(engine) as session:
        junction = session.get(Junction, "A")
        signals = session.query(SignalState).all()

        assert junction.controller_status == "ONLINE"

        assert all(
            signal.actual_state == "RED"
            for signal in signals
        )


def test_restart_invalidates_pending_commands(engine):
    with Session(engine) as session:
        session.add(
            ControllerCommand(
                command_id="pending-1",
                junction_id="A",
                direction="EAST",
                requested_state="GREEN",
                status="PENDING",
            )
        )
        session.commit()

    with Session(engine) as session:
        ControllerRecoveryService(session).recover_after_restart()

    with Session(engine) as session:
        command = session.get(ControllerCommand, "pending-1")

        assert command.status == "FAILED"
