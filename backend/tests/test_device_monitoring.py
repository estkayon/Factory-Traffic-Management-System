
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.controllers.device_status_service import (
    DeviceStatusService,
)
from backend.app.infrastructure.database.base import Base
from backend.app.infrastructure.database.models import (
    AuditLog,
    DeviceStatus,
    Junction,
    SignalState,
)
from backend.app.schemas.device_status import DeviceStatusRequest


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "device_monitoring.db"

    db_engine = create_engine(
        f"sqlite:///{path.as_posix()}",
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

    yield db_engine
    db_engine.dispose()


def update(engine, device_type, direction, status):
    payload = DeviceStatusRequest(
        junction_id="A",
        device_type=device_type,
        direction=direction,
        status=status,
    )

    with Session(engine) as session:
        return DeviceStatusService(session).update(payload)


def test_controller_offline_invalidates_all_actual_signals(engine):
    result = update(
        engine, "SIGNAL_CONTROLLER", None, "OFFLINE"
    )

    assert result["controller_status"] == "DEGRADED"

    with Session(engine) as session:
        signals = session.scalars(select(SignalState)).all()

        assert all(
            signal.actual_state == "UNKNOWN"
            for signal in signals
        )


def test_signal_failure_invalidates_only_affected_direction(engine):
    update(engine, "TRAFFIC_SIGNAL", "EAST", "OFFLINE")

    with Session(engine) as session:
        states = {
            row.direction: row.actual_state
            for row in session.scalars(select(SignalState))
        }

        assert states["EAST"] == "UNKNOWN"
        assert states["NORTH"] == "RED"
        assert session.get(Junction, "A").controller_status == "DEGRADED"


def test_sensor_failure_blocks_normal_execution(engine):
    update(engine, "VEHICLE_SENSOR", "WEST", "OFFLINE")

    with Session(engine) as session:
        junction = session.get(Junction, "A")
        assert junction.controller_status == "DEGRADED"


def test_online_report_does_not_bypass_reconciliation(engine):
    update(engine, "SIGNAL_CONTROLLER", None, "OFFLINE")
    result = update(engine, "SIGNAL_CONTROLLER", None, "ONLINE")

    assert result["controller_status"] == "DEGRADED"

    with Session(engine) as session:
        signals = session.scalars(select(SignalState)).all()
        assert all(s.actual_state == "UNKNOWN" for s in signals)


def test_repeated_status_updates_reuse_device_record(engine):
    update(engine, "VEHICLE_SENSOR", "NORTH", "OFFLINE")
    update(engine, "VEHICLE_SENSOR", "NORTH", "ONLINE")

    with Session(engine) as session:
        devices = session.scalars(select(DeviceStatus)).all()

        assert len(devices) == 1
        assert devices[0].status == "ONLINE"


def test_device_failure_creates_audit_history(engine):
    update(engine, "TRAFFIC_SIGNAL", "SOUTH", "OFFLINE")

    with Session(engine) as session:
        log = session.scalar(
            select(AuditLog)
            .where(AuditLog.event_type == "DEVICE_STATUS_CHANGED")
        )

        assert log is not None
        assert log.details["direction"] == "SOUTH"
        assert log.details["status"] == "OFFLINE"
