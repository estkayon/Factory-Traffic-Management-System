
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    AuditLog,
    Junction,
    Vehicle,
)


DIRECTION_PHASE = {
    "NORTH": "NORTH_SOUTH",
    "SOUTH": "NORTH_SOUTH",
    "EAST": "EAST_WEST",
    "WEST": "EAST_WEST",
}


class ManualControlError(Exception):
    """Raised when a manual control request is invalid."""


class ManualControlService:
    """Persist administrator intent without directly changing signals."""

    def __init__(self, session: Session):
        self.session = session

    def execute(
        self,
        junction_id: str,
        command: str,
        direction: str | None = None,
    ) -> dict:
        """Apply a manual control command transactionally."""

        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        junction = self.session.get(Junction, junction_id)

        if junction is None:
            raise ManualControlError("Junction not found.")

        if command == "MANUAL_GREEN_REQUEST":
            if direction not in DIRECTION_PHASE:
                raise ManualControlError(
                    "A valid direction is required."
                )

            junction.manual_phase = DIRECTION_PHASE[direction]

        elif command == "RETURN_TO_AUTOMATIC":
            if direction is not None:
                raise ManualControlError(
                    "Direction is not allowed for this command."
                )

            junction.manual_phase = None

        else:
            raise ManualControlError("Unsupported control command.")

        emergency_count = self.session.scalar(
            select(func.count())
            .select_from(Vehicle)
            .where(
                Vehicle.junction_id == junction_id,
                Vehicle.vehicle_type == "EMERGENCY",
                Vehicle.is_waiting.is_(True),
            )
        )

        if emergency_count:
            junction.mode = "EMERGENCY"
        elif junction.manual_phase is not None:
            junction.mode = "MANUAL"
        else:
            junction.mode = "AUTOMATIC"

        self.session.add(
            AuditLog(
                junction_id=junction_id,
                event_type="MANUAL_CONTROL_COMMAND",
                description=f"Administrator requested {command}.",
                details={
                    "command": command,
                    "direction": direction,
                    "manual_phase": junction.manual_phase,
                    "effective_mode": junction.mode,
                },
            )
        )

        result = {
            "junction_id": junction.id,
            "mode": junction.mode,
            "manual_phase": junction.manual_phase,
            "command": command,
            "message": (
                "Control preference saved. "
                "Signal changes require safe execution."
            ),
        }

        self.session.commit()
        return result
