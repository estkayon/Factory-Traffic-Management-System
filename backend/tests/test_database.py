
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    Junction,
    SignalState,
)


@pytest.fixture
def db_session():
    """Create an isolated database for each test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    Base.metadata.create_all(engine)

    with Session(engine) as session:
        yield session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_junction_can_be_persisted(db_session):
    junction = Junction(
        id="A",
        name="Factory Junction A",
    )

    db_session.add(junction)
    db_session.commit()

    saved = db_session.get(Junction, "A")

    assert saved is not None
    assert saved.mode == "AUTOMATIC"
    assert saved.active_phase == "ALL_RED"


def test_duplicate_signal_direction_is_rejected(db_session):
    db_session.add(Junction(id="A", name="Factory Junction A"))
    db_session.flush()

    db_session.add_all([
        SignalState(junction_id="A", direction="NORTH"),
        SignalState(junction_id="A", direction="NORTH"),
    ])

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()


def test_all_seven_tables_are_registered():
    expected_tables = {
        "junctions",
        "vehicles",
        "sensor_events",
        "signal_states",
        "controller_commands",
        "device_statuses",
        "audit_logs",
    }

    assert set(Base.metadata.tables.keys()) == expected_tables


def test_signal_state_defaults(db_session):
    db_session.add(Junction(id="A", name="Factory Junction A"))
    db_session.flush()

    db_session.add(
        SignalState(junction_id="A", direction="EAST")
    )
    db_session.commit()

    signal = db_session.scalar(
        select(SignalState).where(
            SignalState.junction_id == "A",
            SignalState.direction == "EAST",
        )
    )

    assert signal is not None
    assert signal.desired_state == "RED"
    assert signal.actual_state == "UNKNOWN"
