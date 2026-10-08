
import pytest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    Junction,
    SignalState,
)
from backend.app.infrastructure.controllers.command_service import (
    ControllerCommandService,
    ControllerCommandError,
    ControllerNotReadyError,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'controller.db').as_posix()}",
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

        session.commit()

    try:
        yield engine
    finally:
        engine.dispose()


def test_command_is_pending_until_ack(db):
    with Session(db) as session:
        service = ControllerCommandService(session)

        command = service.issue_command("A", "NORTH", "GREEN")

        assert command.status == "PENDING"

        signal = session.query(SignalState).filter_by(
            junction_id="A",
            direction="NORTH",
        ).one()

        assert signal.desired_state == "GREEN"
        assert signal.actual_state == "RED"


def test_valid_ack_updates_actual_state(db):
    with Session(db) as session:
        service = ControllerCommandService(session)
        command = service.issue_command("A", "NORTH", "GREEN")

        result = service.acknowledge(
            command.command_id, "A", "ACK", "GREEN"
        )

        assert result.status == "ACKED"

        signal = session.query(SignalState).filter_by(
            junction_id="A", direction="NORTH"
        ).one()

        assert signal.actual_state == "GREEN"


def test_conflicting_green_is_rejected(db):
    with Session(db) as session:
        service = ControllerCommandService(session)

        first = service.issue_command("A", "NORTH", "GREEN")
        service.acknowledge(first.command_id, "A", "ACK", "GREEN")

        with pytest.raises(ControllerNotReadyError):
            service.issue_command("A", "EAST", "GREEN")

        session.rollback()


def test_unknown_opposing_signal_blocks_green(db):
    with Session(db) as session:
        session.query(SignalState).filter_by(
            junction_id="A", direction="EAST"
        ).one().actual_state = "UNKNOWN"
        session.commit()

    with Session(db) as session:
        service = ControllerCommandService(session)

        with pytest.raises(ControllerNotReadyError):
            service.issue_command("A", "NORTH", "GREEN")

        session.rollback()


def test_duplicate_ack_is_rejected(db):
    with Session(db) as session:
        service = ControllerCommandService(session)

        command = service.issue_command("A", "NORTH", "GREEN")

        service.acknowledge(command.command_id, "A", "ACK", "GREEN")

        with pytest.raises(ControllerCommandError):
            service.acknowledge(command.command_id, "A", "ACK", "GREEN")

        session.rollback()


def test_nack_causes_degraded_mode(db):
    with Session(db) as session:
        service = ControllerCommandService(session)

        command = service.issue_command("A", "NORTH", "GREEN")

        result = service.acknowledge(
            command.command_id, "A", "NACK", "RED"
        )

        assert result.status == "FAILED"
        assert session.get(Junction, "A").controller_status == "DEGRADED"


def test_offline_controller_rejects_commands(db):
    with Session(db) as session:
        session.get(Junction, "A").controller_status = "OFFLINE"
        session.commit()

    with Session(db) as session:
        service = ControllerCommandService(session)

        with pytest.raises(ControllerNotReadyError):
            service.issue_command("A", "NORTH", "GREEN")

        session.rollback()


def test_only_one_pending_command_per_junction(db):
    with Session(db) as session:
        service = ControllerCommandService(session)

        service.issue_command("A", "NORTH", "GREEN")

        with pytest.raises(ControllerNotReadyError):
            service.issue_command("A", "SOUTH", "GREEN")

        session.rollback()
