
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    AuditLog,
    DeviceStatus,
    Junction,
    SignalState,
)
from backend.app.schemas.device_status import (
    DeviceStatusRequest,
)


def utc_now() -> datetime:
    """Return naive UTC for the project's SQLite storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DeviceStatusError(Exception):
    """Raised for invalid device status operations."""


class DeviceStatusService:
    """Persist device health and enforce conservative failure behavior."""

    def __init__(self, session: Session):
        self.session = session

    def update(self, event: DeviceStatusRequest) -> dict:
        """Apply a simulated device health event atomically."""

        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        junction = self.session.get(Junction, event.junction_id)

        if junction is None:
            raise DeviceStatusError("Junction not found.")

        direction = (
            event.direction.value
            if event.direction is not None
            else None
        )

        # SQLite has no portable NULL-aware unique constraint
        # for this schema, so the transaction serializes upserts.
        records = self.session.scalars(
            select(DeviceStatus).where(
                DeviceStatus.junction_id == event.junction_id,
                DeviceStatus.device_type == event.device_type.value,
            )
        ).all()

        device = next(
            (record for record in records
             if record.direction == direction),
            None,
        )

        if device is None:
            device = DeviceStatus(
                junction_id=event.junction_id,
                device_type=event.device_type.value,
                direction=direction,
            )
            self.session.add(device)

        device.status = event.status.value
        device.last_seen_at = utc_now()

        if event.status.value != "ONLINE":
            # Loss of a required device makes normal operation
            # untrustworthy. This does not physically turn lights RED.
            junction.controller_status = "DEGRADED"

            signals = self.session.scalars(
                select(SignalState).where(
                    SignalState.junction_id == event.junction_id
                )
            ).all()

            for signal in signals:
                if (
                    event.device_type.value == "SIGNAL_CONTROLLER"
                    or (
                        event.device_type.value == "TRAFFIC_SIGNAL"
                        and signal.direction == direction
                    )
                ):
                    signal.actual_state = "UNKNOWN"
                    signal.confirmed_at = None

        # ONLINE is only a connectivity observation.
        # It must never bypass controller reconciliation.

        self.session.add(
            AuditLog(
                junction_id=event.junction_id,
                event_type="DEVICE_STATUS_CHANGED",
                description=(
                    f"{event.device_type.value} "
                    f"{direction or 'JUNCTION'}: {event.status.value}"
                ),
                details={
                    "device_type": event.device_type.value,
                    "direction": direction,
                    "status": event.status.value,
                },
            )
        )

        result = {
            "junction_id": event.junction_id,
            "device_type": event.device_type.value,
            "direction": direction,
            "status": event.status.value,
            "controller_status": junction.controller_status,
            "message": "Device status recorded.",
        }

        self.session.commit()
        return result
