
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    AuditLog,
    Junction,
    SensorEvent,
    Vehicle,
)
from backend.app.schemas.sensor_event import (
    SensorEventRequest,
    SensorEventResponse,
    SensorEventType,
)

DIRECTIONS = ("NORTH", "SOUTH", "EAST", "WEST")


class UnknownJunctionError(Exception):
    pass


class EventIdConflictError(Exception):
    pass


def to_db_time(value: datetime) -> datetime:
    """Normalize timestamps to naive UTC for SQLite storage."""
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class VehicleEventService:
    """Process sensor events and maintain persistent vehicle queues."""

    def __init__(self, session: Session):
        self.session = session

    def process(self, event: SensorEventRequest) -> SensorEventResponse:
        # Serialize concurrent SQLite writers before reading queue state.
        # The caller must provide a fresh, unmodified session.
        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        junction = self.session.get(Junction, event.junction_id)
        if junction is None:
            raise UnknownJunctionError(event.junction_id)

        previous_event = self.session.get(SensorEvent, event.event_id)

        if previous_event is not None:
            if not self._same_event(previous_event, event):
                raise EventIdConflictError(event.event_id)

            return self._response(
                event,
                "DUPLICATE",
                "Event already processed. No changes applied.",
                junction,
            )

        last_event = self.session.scalar(
            select(SensorEvent)
            .where(
                SensorEvent.junction_id == event.junction_id,
                SensorEvent.vehicle_id == event.vehicle_id,
            )
            .order_by(SensorEvent.sequence_no.desc())
            .limit(1)
        )

        rejection = None

        if (
            last_event is not None
            and event.sequence_no <= last_event.sequence_no
        ):
            rejection = "Stale or out-of-order sequence number."

        if (
            event.event_type == SensorEventType.VEHICLE_ARRIVED
            and event.vehicle_type is None
        ):
            rejection = rejection or "Vehicle type is required for arrival."

        vehicle = self.session.scalar(
            select(Vehicle).where(
                Vehicle.junction_id == event.junction_id,
                Vehicle.vehicle_id == event.vehicle_id,
            )
        )

        if rejection is None:
            if event.event_type == SensorEventType.VEHICLE_ARRIVED:
                if vehicle is not None:
                    rejection = "Vehicle already registered at this junction."
            else:
                if vehicle is None or not vehicle.is_waiting:
                    rejection = "No active vehicle found to clear."
                elif vehicle.direction != event.direction.value:
                    rejection = "Clearance direction does not match arrival."

        event_record = SensorEvent(
            event_id=event.event_id,
            junction_id=event.junction_id,
            direction=event.direction.value,
            event_type=event.event_type.value,
            vehicle_id=event.vehicle_id,
            vehicle_type=(
                event.vehicle_type.value
                if event.vehicle_type is not None
                else None
            ),
            sequence_no=event.sequence_no,
            sensor_timestamp=to_db_time(event.timestamp),
            processing_status="REJECTED" if rejection else "ACCEPTED",
            rejection_reason=rejection,
        )

        self.session.add(event_record)

        if rejection:
            self._audit(
                event.junction_id,
                "SENSOR_EVENT_REJECTED",
                rejection,
                event.event_id,
            )
            self.session.commit()

            return self._response(
                event, "REJECTED", rejection, junction
            )

        if event.event_type == SensorEventType.VEHICLE_ARRIVED:
            self.session.add(
                Vehicle(
                    junction_id=event.junction_id,
                    vehicle_id=event.vehicle_id,
                    direction=event.direction.value,
                    vehicle_type=event.vehicle_type.value,
                    arrived_at=to_db_time(
                        datetime.now(timezone.utc)
                    ),
                    is_waiting=True,
                )
            )
            audit_type = "VEHICLE_DETECTED"
            description = f"Vehicle {event.vehicle_id} arrived."

            if event.vehicle_type.value == "EMERGENCY":
                junction.mode = "EMERGENCY"
                self._audit(
                    event.junction_id,
                    "EMERGENCY_DETECTED",
                    f"Emergency vehicle approaching {event.direction.value}.",
                    event.event_id,
                )

        else:
            vehicle.is_waiting = False
            vehicle.cleared_at = to_db_time(
                datetime.now(timezone.utc)
            )

            audit_type = "VEHICLE_CLEARED"
            description = f"Vehicle {event.vehicle_id} cleared."

            if vehicle.vehicle_type == "EMERGENCY":
                self._audit(
                    event.junction_id,
                    "EMERGENCY_CLEARED",
                    description,
                    event.event_id,
                )
                self.session.flush()

                remaining_emergencies = self.session.scalar(
                    select(func.count())
                    .select_from(Vehicle)
                    .where(
                        Vehicle.junction_id == event.junction_id,
                        Vehicle.vehicle_type == "EMERGENCY",
                        Vehicle.is_waiting.is_(True),
                    )
                )

                if remaining_emergencies == 0:
                    junction.mode = (
                        "MANUAL"
                        if junction.manual_phase is not None
                        else "AUTOMATIC"
                    )

        self._audit(
            event.junction_id,
            audit_type,
            description,
            event.event_id,
        )

        self.session.commit()

        return self._response(
            event,
            "ACCEPTED",
            "Vehicle event processed successfully.",
            junction,
        )

    def _same_event(
        self,
        stored: SensorEvent,
        incoming: SensorEventRequest,
    ) -> bool:
        return (
            stored.junction_id == incoming.junction_id
            and stored.vehicle_id == incoming.vehicle_id
            and stored.direction == incoming.direction.value
            and stored.event_type == incoming.event_type.value
            and stored.sequence_no == incoming.sequence_no
            and stored.vehicle_type == (
                incoming.vehicle_type.value
                if incoming.vehicle_type is not None
                else None
            )
            and stored.sensor_timestamp == to_db_time(incoming.timestamp)
        )

    def _audit(
        self,
        junction_id: str,
        event_type: str,
        description: str,
        event_id: str,
    ) -> None:
        self.session.add(
            AuditLog(
                junction_id=junction_id,
                event_type=event_type,
                description=description,
                details={"sensor_event_id": event_id},
            )
        )

    def _response(
        self,
        event: SensorEventRequest,
        status: str,
        message: str,
        junction: Junction,
    ) -> SensorEventResponse:
        count = self.session.scalar(
            select(func.count())
            .select_from(Vehicle)
            .where(
                Vehicle.junction_id == event.junction_id,
                Vehicle.direction == event.direction.value,
                Vehicle.is_waiting.is_(True),
            )
        )

        return SensorEventResponse(
            event_id=event.event_id,
            status=status,
            message=message,
            queue_size=count,
            emergency_active=junction.mode == "EMERGENCY",
        )
