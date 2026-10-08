
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def utc_now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


def new_id() -> str:
    """Generate an identifier for a database record."""
    return str(uuid4())


class Junction(Base):
    __tablename__ = "junctions"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    mode: Mapped[str] = mapped_column(
        String(20), default="AUTOMATIC", nullable=False
    )

    active_phase: Mapped[str] = mapped_column(
        String(30), default="ALL_RED", nullable=False
    )

    transition_stage: Mapped[str] = mapped_column(
        String(30), default="IDLE", nullable=False
    )

    target_phase: Mapped[str | None] = mapped_column(String(30))
    phase_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    transition_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    manual_phase: Mapped[str | None] = mapped_column(
    String(30),
    nullable=True,
    )

    controller_status: Mapped[str] = mapped_column(
        String(20), default="UNKNOWN", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )

    junction_id: Mapped[str] = mapped_column(
        ForeignKey("junctions.id"), nullable=False, index=True
    )

    vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    vehicle_type: Mapped[str] = mapped_column(String(30), nullable=False)

    is_waiting: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    arrived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint(
            "junction_id", "vehicle_id",
            name="uq_vehicle_per_junction"
        ),
    )


class SensorEvent(Base):
    __tablename__ = "sensor_events"

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)

    junction_id: Mapped[str] = mapped_column(
        ForeignKey("junctions.id"), nullable=False, index=True
    )

    vehicle_id: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    vehicle_type: Mapped[str | None] = mapped_column(String(30))
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)

    sensor_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    processing_status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(255))


class SignalState(Base):
    __tablename__ = "signal_states"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )

    junction_id: Mapped[str] = mapped_column(
        ForeignKey("junctions.id"), nullable=False
    )

    direction: Mapped[str] = mapped_column(String(10), nullable=False)

    desired_state: Mapped[str] = mapped_column(
        String(10), default="RED", nullable=False
    )

    actual_state: Mapped[str] = mapped_column(
        String(10), default="UNKNOWN", nullable=False
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint(
            "junction_id", "direction",
            name="uq_signal_direction"
        ),
    )


class ControllerCommand(Base):
    __tablename__ = "controller_commands"

    command_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )

    junction_id: Mapped[str] = mapped_column(
        ForeignKey("junctions.id"), nullable=False, index=True
    )

    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    requested_state: Mapped[str] = mapped_column(
        String(10), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), default="PENDING", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    retry_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )


class DeviceStatus(Base):
    __tablename__ = "device_statuses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )

    junction_id: Mapped[str] = mapped_column(
        ForeignKey("junctions.id"), nullable=False
    )

    device_type: Mapped[str] = mapped_column(String(30), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(10))

    status: Mapped[str] = mapped_column(
        String(20), default="UNKNOWN", nullable=False
    )

    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    junction_id: Mapped[str] = mapped_column(
        ForeignKey("junctions.id"), nullable=False, index=True
    )

    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )

    description: Mapped[str] = mapped_column(
        String(500), nullable=False
    )

    details: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
