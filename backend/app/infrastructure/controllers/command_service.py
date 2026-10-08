
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    AuditLog,
    ControllerCommand,
    Junction,
    SignalState,
)


DIRECTIONS = ("NORTH", "SOUTH", "EAST", "WEST")

OPPOSING = {
    "NORTH": {"EAST", "WEST"},
    "SOUTH": {"EAST", "WEST"},
    "EAST": {"NORTH", "SOUTH"},
    "WEST": {"NORTH", "SOUTH"},
}

SIGNAL_COLORS = {"RED", "YELLOW", "GREEN"}


class ControllerError(Exception):
    """Base exception for controller operations."""


class ControllerNotReadyError(ControllerError):
    """Controller cannot safely execute the requested operation."""


class ControllerCommandError(ControllerError):
    """Command or acknowledgement is invalid."""


def utc_now() -> datetime:
    """Return naive UTC, matching SQLite timestamp storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ControllerCommandService:
    """
    Persist controller commands and process acknowledgements.

    A command changes desired state only.
    Confirmed actual state changes exclusively after a valid ACK.

    SQLite operations are serialized with BEGIN IMMEDIATE.
    """

    def __init__(self, session: Session):
        self.session = session

    def _lock(self) -> None:
        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _log(
        self,
        junction_id: str,
        event_type: str,
        description: str,
        command_id: str | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                junction_id=junction_id,
                event_type=event_type,
                description=description,
                details={"command_id": command_id},
            )
        )

    def _signals(self, junction_id: str) -> dict[str, SignalState]:
        signals = self.session.scalars(
            select(SignalState).where(
                SignalState.junction_id == junction_id
            )
        ).all()

        result = {signal.direction: signal for signal in signals}

        if set(result) != set(DIRECTIONS):
            raise ControllerNotReadyError(
                "Complete signal configuration is required."
            )

        return result

    def issue_command(
        self,
        junction_id: str,
        direction: str,
        requested_state: str,
        timeout_seconds: int = 10,
    ) -> ControllerCommand:
        """
        Request one signal change.

        A GREEN request requires confirmed RED on every
        conflicting direction. Only one pending command
        is allowed per junction in this assessment version.
        """
        if direction not in DIRECTIONS:
            raise ControllerCommandError("Invalid direction.")

        if requested_state not in SIGNAL_COLORS:
            raise ControllerCommandError("Invalid signal state.")

        if timeout_seconds <= 0:
            raise ControllerCommandError("Timeout must be positive.")

        self._lock()

        junction = self.session.get(Junction, junction_id)

        if junction is None:
            raise ControllerCommandError("Unknown junction.")

        if junction.controller_status != "ONLINE":
            raise ControllerNotReadyError(
                "Controller is not confirmed ONLINE."
            )

        existing_pending = self.session.scalar(
            select(ControllerCommand).where(
                ControllerCommand.junction_id == junction_id,
                ControllerCommand.status == "PENDING",
            )
        )

        if existing_pending is not None:
            raise ControllerNotReadyError(
                "A controller command is already pending."
            )

        signals = self._signals(junction_id)
        signal = signals[direction]

        if requested_state == "GREEN":
            if any(
                signals[other].actual_state != "RED"
                for other in OPPOSING[direction]
            ):
                raise ControllerNotReadyError(
                    "Conflicting signals must be confirmed RED."
                )

        now = utc_now()

        command = ControllerCommand(
            junction_id=junction_id,
            direction=direction,
            requested_state=requested_state,
            status="PENDING",
            created_at=now,
            expires_at=now + timedelta(seconds=timeout_seconds),
        )

        signal.desired_state = requested_state

        self.session.add(command)
        self.session.flush()

        self._log(
            junction_id,
            "SIGNAL_STATE_REQUESTED",
            f"{direction} requested {requested_state}.",
            command.command_id,
        )

        self.session.commit()
        return command

    def acknowledge(
        self,
        command_id: str,
        junction_id: str,
        status: str,
        actual_state: str | None,
    ) -> ControllerCommand:
        """Process an acknowledgement exactly once."""
        if status not in {"ACK", "NACK"}:
            raise ControllerCommandError("Invalid acknowledgement status.")

        if actual_state is not None and actual_state not in SIGNAL_COLORS:
            raise ControllerCommandError("Invalid actual signal state.")

        self._lock()

        command = self.session.get(ControllerCommand, command_id)

        if command is None or command.junction_id != junction_id:
            raise ControllerCommandError("Unknown controller command.")

        if command.status != "PENDING":
            raise ControllerCommandError(
                f"Command is already {command.status}."
            )

        junction = self.session.get(Junction, junction_id)
        signals = self._signals(junction_id)
        signal = signals[command.direction]

        now = utc_now()

        if command.expires_at is not None and now >= command.expires_at:
            command.status = "TIMEOUT"
            junction.controller_status = "DEGRADED"
            signal.actual_state = "UNKNOWN"

            self._log(
                junction_id,
                "CONTROLLER_TIMEOUT",
                "Acknowledgement arrived after the deadline.",
                command_id,
            )
            self.session.commit()
            return command

        command.acknowledged_at = now

        if status == "ACK" and actual_state == command.requested_state:
            command.status = "ACKED"
            signal.actual_state = actual_state
            signal.confirmed_at = now

            self._log(
                junction_id,
                "SIGNAL_STATE_CONFIRMED",
                f"{command.direction} confirmed {actual_state}.",
                command_id,
            )
        else:
            command.status = "FAILED"
            junction.controller_status = "DEGRADED"
            signal.actual_state = "UNKNOWN"

            self._log(
                junction_id,
                "CONTROLLER_COMMAND_FAILED",
                f"Command {command_id} was rejected or mismatched.",
                command_id,
            )

        self.session.commit()
        return command

    def expire_pending(self, junction_id: str) -> int:
        """Mark expired commands and invalidate unconfirmed states."""
        self._lock()

        junction = self.session.get(Junction, junction_id)

        if junction is None:
            raise ControllerCommandError("Unknown junction.")

        pending = self.session.scalars(
            select(ControllerCommand).where(
                ControllerCommand.junction_id == junction_id,
                ControllerCommand.status == "PENDING",
                ControllerCommand.expires_at <= utc_now(),
            )
        ).all()

        if pending:
            signals = self._signals(junction_id)
            junction.controller_status = "DEGRADED"

            for command in pending:
                command.status = "TIMEOUT"
                signals[command.direction].actual_state = "UNKNOWN"

                self._log(
                    junction_id,
                    "CONTROLLER_TIMEOUT",
                    f"Command {command.command_id} timed out.",
                    command.command_id,
                )

        self.session.commit()
        return len(pending)
