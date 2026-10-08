
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    AuditLog,
    ControllerCommand,
    Junction,
    SignalState,
)

DIRECTIONS = {"NORTH", "SOUTH", "EAST", "WEST"}


class ControllerRecoveryService:
    """
    Manage controller state after restart and reconciliation.

    This service never assumes a physical signal is RED
    merely because the backend requested RED.
    """

    def __init__(self, session: Session):
        self.session = session

    def recover_after_restart(self) -> None:
        """Invalidate physical state assumptions after a restart."""

        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        junctions = self.session.scalars(select(Junction)).all()

        for junction in junctions:
            junction.controller_status = "UNKNOWN"

            # ALL_RED describes the new safe target.
            # It does not claim physical signals are RED.
            junction.active_phase = "ALL_RED"
            junction.transition_stage = "IDLE"
            junction.target_phase = None
            junction.transition_due_at = None
            junction.phase_started_at = None

            signals = self.session.scalars(
                select(SignalState).where(
                    SignalState.junction_id == junction.id
                )
            ).all()

            for signal in signals:
                signal.desired_state = "RED"
                signal.actual_state = "UNKNOWN"
                signal.confirmed_at = None

            pending = self.session.scalars(
                select(ControllerCommand).where(
                    ControllerCommand.junction_id == junction.id,
                    ControllerCommand.status == "PENDING",
                )
            ).all()

            for command in pending:
                command.status = "FAILED"

            self.session.add(
                AuditLog(
                    junction_id=junction.id,
                    event_type="BACKEND_RECOVERY",
                    description=(
                        "Backend restarted. Physical signal states "
                        "require reconciliation."
                    ),
                    details={
                        "invalidated_commands": len(pending),
                    },
                )
            )

        self.session.commit()

    def reconcile_all_red(self, junction_id: str) -> None:
        """
        Accept a fresh ALL_RED confirmation from the simulator.

        The simulator must verify every physical signal
        before invoking this operation.
        """

        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

        junction = self.session.get(Junction, junction_id)

        if junction is None:
            raise ValueError("Unknown junction.")

        pending = self.session.scalars(
            select(ControllerCommand).where(
                ControllerCommand.junction_id == junction_id,
                ControllerCommand.status == "PENDING",
            )
        ).all()

        if pending:
            raise ValueError(
                "Cannot reconcile while controller commands are pending."
            )

        signals = self.session.scalars(
            select(SignalState).where(
                SignalState.junction_id == junction_id
            )
        ).all()

        if {signal.direction for signal in signals} != DIRECTIONS:
            raise ValueError("Incomplete signal configuration.")

        for signal in signals:
            signal.desired_state = "RED"
            signal.actual_state = "RED"

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        for signal in signals:
            signal.confirmed_at = now

        junction.controller_status = "ONLINE"
        junction.active_phase = "ALL_RED"
        junction.transition_stage = "IDLE"
        junction.target_phase = None
        junction.transition_due_at = None
        junction.phase_started_at = None

        self.session.add(
            AuditLog(
                junction_id=junction_id,
                event_type="CONTROLLER_RECONCILED",
                description="Simulator confirmed all signals RED.",
                details={"confirmed_state": "ALL_RED"},
            )
        )

        self.session.commit()
