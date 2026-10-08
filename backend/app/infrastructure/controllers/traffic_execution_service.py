"""Persist and execute ACK-gated traffic transitions for the REST simulator.

The service controls *requested* states. A controller acknowledgement is the
only mechanism that updates the confirmed (actual) signal state. This is not
physical interlocking and must not be used to control real traffic equipment.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.domain.entities.control import ControlContext
from backend.app.domain.entities.traffic import (
    Direction,
    Phase,
    TrafficState,
    VehicleType,
    WaitingVehicle,
)
from backend.app.domain.services.junction_orchestrator import JunctionOrchestrator
from backend.app.domain.services.traffic_scheduler import TrafficScheduler
from backend.app.infrastructure.database.models import (
    AuditLog,
    ControllerCommand,
    Junction,
    SignalState,
    Vehicle,
)

PHASE_DIRECTIONS = {
    "NORTH_SOUTH": ("NORTH", "SOUTH"),
    "EAST_WEST": ("EAST", "WEST"),
}
DIRECTIONS = ("NORTH", "SOUTH", "EAST", "WEST")
YELLOW_SECONDS = 5
ALL_RED_SECONDS = 2
ACK_TIMEOUT_SECONDS = 60  # Relaxed for interactive Swagger demonstration.


def utc_now() -> datetime:
    """Return naive UTC as used by the project's SQLite ORM records."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TrafficExecutionError(Exception):
    """Invalid junction or incomplete signal configuration."""


class TrafficExecutionService:
    """Advance one serialized, persistent control step per call to tick()."""

    def __init__(self, session: Session):
        self.session = session
        self.scheduler = TrafficScheduler()
        self.orchestrator = JunctionOrchestrator(scheduler=self.scheduler)

    def tick(self, junction_id: str, now: datetime | None = None) -> dict:
        now = now or utc_now()
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)

        # A fresh SQLAlchemy session is required for this SQLite transaction.
        self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            junction = self.session.get(Junction, junction_id)
            if junction is None:
                raise TrafficExecutionError("Unknown junction.")

            signals = {
                signal.direction: signal
                for signal in self.session.scalars(
                    select(SignalState).where(SignalState.junction_id == junction_id)
                ).all()
            }
            if set(signals) != set(DIRECTIONS):
                raise TrafficExecutionError("Incomplete traffic signal configuration.")

            result = self._execute(junction, signals, now)
            self.session.commit()
            return result
        except Exception:
            self.session.rollback()
            raise

    def _execute(self, junction: Junction, signals: dict, now: datetime) -> dict:
        if junction.controller_status != "ONLINE":
            return self._result(junction, "CONTROLLER_NOT_READY")

        pending = self.session.scalar(
            select(ControllerCommand).where(
                ControllerCommand.junction_id == junction.id,
                ControllerCommand.status == "PENDING",
            )
        )
        if pending is not None:
            if pending.expires_at is not None and now >= pending.expires_at:
                pending.status = "TIMEOUT"
                junction.controller_status = "DEGRADED"
                signals[pending.direction].actual_state = "UNKNOWN"
                self._log(junction.id, "CONTROLLER_TIMEOUT", f"Command {pending.command_id} timed out.")
                return self._result(junction, "COMMAND_TIMEOUT")
            # Resolve an in-flight GREEN before attempting emergency clearance.
            return self._result(junction, "WAITING_FOR_ACK", pending.command_id)

        stage = junction.transition_stage
        if stage == "IDLE":
            return self._start_transition(junction, signals, now)

        if stage == "WAIT_YELLOW":
            if self._all_confirmed(signals, junction.active_phase, "YELLOW"):
                junction.transition_stage = "YELLOW_HOLD"
                junction.transition_due_at = now + timedelta(seconds=YELLOW_SECONDS)
                return self._result(junction, "YELLOW_HOLD")
            return self._issue_next(junction, signals, junction.active_phase, "YELLOW", now)

        if stage == "YELLOW_HOLD":
            if not self._all_confirmed(signals, junction.active_phase, "YELLOW"):
                return self._degrade(junction, "YELLOW state lost.")
            if junction.transition_due_at is None:
                return self._degrade(junction, "Missing YELLOW transition deadline.")
            if now < junction.transition_due_at:
                return self._result(junction, "YELLOW_HOLD")
            junction.transition_stage = "WAIT_RED"
            return self._issue_next(junction, signals, junction.active_phase, "RED", now)

        if stage == "WAIT_RED":
            if self._all_confirmed(signals, junction.active_phase, "RED"):
                if not self._all_red(signals):
                    return self._degrade(junction, "ALL_RED confirmation failed.")
                junction.active_phase = "ALL_RED"
                junction.transition_stage = "RED_HOLD"
                junction.transition_due_at = now + timedelta(seconds=ALL_RED_SECONDS)
                return self._result(junction, "ALL_RED_HOLD")
            return self._issue_next(junction, signals, junction.active_phase, "RED", now)

        if stage == "RED_HOLD":
            if not self._all_red(signals):
                return self._degrade(junction, "ALL_RED clearance not confirmed.")
            if junction.transition_due_at is None:
                return self._degrade(junction, "Missing ALL_RED clearance deadline.")
            if now < junction.transition_due_at:
                return self._result(junction, "ALL_RED_HOLD")

            # A new emergency/manual request can supersede the original target.
            new_target, effective_mode = self._select_current_priority(junction, now)
            junction.mode = effective_mode
            if new_target is None:
                junction.active_phase = "ALL_RED"
                junction.target_phase = None
                junction.transition_stage = "IDLE"
                junction.transition_due_at = None
                junction.phase_started_at = None
                self._log(junction.id, "NO_TRAFFIC_PENDING", "No traffic request; remaining ALL_RED.")
                return self._result(junction, "ALL_RED_IDLE")

            if junction.target_phase != new_target.value:
                old_target = junction.target_phase
                junction.target_phase = new_target.value
                self._log(
                    junction.id,
                    "EMERGENCY_PRIORITY_REEVALUATED" if effective_mode == "EMERGENCY" else "TRAFFIC_PRIORITY_REEVALUATED",
                    f"Target changed from {old_target} to {new_target.value}.",
                )
            junction.transition_stage = "WAIT_GREEN"
            return self._issue_next(junction, signals, new_target.value, "GREEN", now)

        if stage == "WAIT_GREEN":
            target = junction.target_phase
            if target not in PHASE_DIRECTIONS:
                return self._degrade(junction, "Invalid GREEN target phase.")

            selected, effective_mode = self._select_current_priority(junction, now)
            if effective_mode == "EMERGENCY" and selected is not None and selected.value != target:
                junction.mode = "EMERGENCY"
                self._log(junction.id, "EMERGENCY_PREEMPTION", f"Aborting GREEN activation of {target}.")
                return self._begin_green_abort(junction, signals, target, now)

            if self._all_confirmed(signals, target, "GREEN"):
                if any(
                    signals[d].actual_state != "RED"
                    for d in DIRECTIONS if d not in PHASE_DIRECTIONS[target]
                ):
                    return self._degrade(junction, "Conflicting signal is not RED.")
                junction.active_phase = target
                junction.target_phase = None
                junction.transition_stage = "IDLE"
                junction.transition_due_at = None
                junction.phase_started_at = now
                self._log(junction.id, "PHASE_ACTIVATED", f"Phase {target} confirmed GREEN.")
                return self._result(junction, "PHASE_ACTIVE")
            return self._issue_next(junction, signals, target, "GREEN", now)

        if stage == "ABORT_WAIT_YELLOW":
            return self._continue_green_abort(junction, signals, now)

        if stage == "ABORT_YELLOW_HOLD":
            if junction.transition_due_at is None:
                return self._degrade(junction, "Missing abort YELLOW deadline.")
            if any(s.actual_state not in ("RED", "YELLOW") for s in signals.values()):
                return self._degrade(junction, "Unsafe state during abort YELLOW hold.")
            if now < junction.transition_due_at:
                return self._result(junction, "ABORT_YELLOW_HOLD")
            junction.transition_stage = "ABORT_WAIT_RED"
            return self._continue_green_abort(junction, signals, now)

        if stage == "ABORT_WAIT_RED":
            return self._continue_green_abort(junction, signals, now)

        return self._degrade(junction, f"Invalid transition stage: {stage}")

    def _select_current_priority(self, junction: Junction, now: datetime) -> tuple[Phase | None, str]:
        """Reevaluate the latest persisted traffic and manual-control intent."""
        waiting = self._waiting_vehicles(junction.id)
        context = ControlContext(
            manual_phase=Phase(junction.manual_phase) if junction.manual_phase is not None else None
        )
        decision = self.orchestrator.evaluate(
            state=TrafficState(phase=Phase.ALL_RED),
            context=context,
            vehicles=waiting,
            now=now.replace(tzinfo=timezone.utc),
        )
        return decision.selected_phase, decision.mode.value

    def _waiting_vehicles(self, junction_id: str) -> list[WaitingVehicle]:
        vehicles = self.session.scalars(
            select(Vehicle).where(
                Vehicle.junction_id == junction_id,
                Vehicle.is_waiting.is_(True),
            )
        ).all()
        return [
            WaitingVehicle(
                vehicle_id=v.vehicle_id,
                direction=Direction(v.direction),
                vehicle_type=VehicleType(v.vehicle_type),
                arrived_at=v.arrived_at.replace(tzinfo=timezone.utc),
            )
            for v in vehicles
        ]

    def _start_transition(self, junction: Junction, signals: dict, now: datetime) -> dict:
        """Select the phase; always execute it via the ACK-gated sequence."""
        context = ControlContext(
            manual_phase=Phase(junction.manual_phase) if junction.manual_phase is not None else None
        )
        state = TrafficState(
            phase=Phase(junction.active_phase),
            phase_started_at=(
                junction.phase_started_at.replace(tzinfo=timezone.utc)
                if junction.phase_started_at is not None else None
            ),
        )
        decision = self.orchestrator.evaluate(
            state=state,
            context=context,
            vehicles=self._waiting_vehicles(junction.id),
            now=now.replace(tzinfo=timezone.utc),
        )
        junction.mode = decision.mode.value
        if decision.reason == "Minimum green duration not reached.":
            return self._result(junction, "MINIMUM_GREEN_ACTIVE")

        selected = decision.selected_phase
        current = Phase(junction.active_phase)
        if selected is None or selected == current:
            return self._result(junction, "NO_SWITCH_NEEDED")

        junction.target_phase = selected.value
        if current == Phase.ALL_RED:
            if not self._all_red(signals):
                return self._degrade(junction, "Initial ALL_RED is not confirmed.")
            junction.transition_stage = "RED_HOLD"
            junction.transition_due_at = now + timedelta(seconds=ALL_RED_SECONDS)
            return self._result(junction, "ALL_RED_HOLD")

        junction.transition_stage = "WAIT_YELLOW"
        self._log(
            junction.id,
            "SIGNAL_TRANSITION_STARTED",
            f"{current.value} to {selected.value}. Control mode: {decision.mode.value}.",
        )
        return self._issue_next(junction, signals, current.value, "YELLOW", now)

    def _begin_green_abort(self, junction: Junction, signals: dict, interrupted_phase: str, now: datetime) -> dict:
        """Abort partial GREEN, but never assume an in-flight command was canceled."""
        if interrupted_phase not in PHASE_DIRECTIONS:
            return self._degrade(junction, "Invalid interrupted phase.")
        if any(s.actual_state not in ("RED", "GREEN") for s in signals.values()):
            return self._degrade(junction, "Unknown signal during GREEN interruption.")
        if self._all_red(signals):
            return self._enter_all_red_hold(junction, now)
        junction.transition_stage = "ABORT_WAIT_YELLOW"
        return self._continue_green_abort(junction, signals, now)

    def _continue_green_abort(self, junction: Junction, signals: dict, now: datetime) -> dict:
        """Convert any partially confirmed GREEN to YELLOW, hold, then RED."""
        stage = junction.transition_stage
        if stage == "ABORT_WAIT_YELLOW":
            if any(s.actual_state not in ("RED", "GREEN", "YELLOW") for s in signals.values()):
                return self._degrade(junction, "Unknown state during GREEN abort.")
            previous, next_color = "GREEN", "YELLOW"
        elif stage == "ABORT_WAIT_RED":
            if any(s.actual_state not in ("RED", "YELLOW") for s in signals.values()):
                return self._degrade(junction, "Unexpected GREEN during RED abort.")
            previous, next_color = "YELLOW", "RED"
        else:
            return self._degrade(junction, "Invalid abort transition stage.")

        for direction in DIRECTIONS:
            signal = signals[direction]
            if signal.actual_state != previous:
                continue
            command = ControllerCommand(
                junction_id=junction.id,
                direction=direction,
                requested_state=next_color,
                status="PENDING",
                created_at=now,
                expires_at=now + timedelta(seconds=ACK_TIMEOUT_SECONDS),
            )
            signal.desired_state = next_color
            self.session.add(command)
            self.session.flush()
            self._log(
                junction.id,
                "EMERGENCY_CLEARANCE_COMMAND",
                f"{direction} requested {next_color}.",
                command.command_id,
            )
            return self._result(junction, "COMMAND_PENDING", command.command_id)

        if stage == "ABORT_WAIT_YELLOW":
            junction.transition_stage = "ABORT_YELLOW_HOLD"
            junction.transition_due_at = now + timedelta(seconds=YELLOW_SECONDS)
            return self._result(junction, "ABORT_YELLOW_HOLD")

        if not self._all_red(signals):
            return self._degrade(junction, "Aborted phase is not fully RED.")
        self._log(junction.id, "EMERGENCY_CLEARANCE_COMPLETE", "Interrupted phase confirmed ALL_RED.")
        return self._enter_all_red_hold(junction, now)

    def _enter_all_red_hold(self, junction: Junction, now: datetime) -> dict:
        junction.active_phase = "ALL_RED"
        junction.target_phase = None
        junction.transition_stage = "RED_HOLD"
        junction.transition_due_at = now + timedelta(seconds=ALL_RED_SECONDS)
        junction.phase_started_at = None
        return self._result(junction, "ALL_RED_HOLD")

    def _issue_next(
        self,
        junction: Junction,
        signals: dict,
        phase: str | None,
        requested_state: str,
        now: datetime,
    ) -> dict:
        """Issue no more than one signal command during the current tick."""
        if phase not in PHASE_DIRECTIONS:
            return self._degrade(junction, "Invalid target phase.")
        if requested_state == "GREEN" and any(
            s.actual_state != "RED"
            for direction, s in signals.items()
            if direction not in PHASE_DIRECTIONS[phase]
        ):
            return self._degrade(junction, "Conflicting signal is not RED.")

        previous = {"YELLOW": "GREEN", "RED": "YELLOW", "GREEN": "RED"}.get(requested_state)
        if previous is None:
            return self._degrade(junction, "Unsupported signal state request.")
        for direction in PHASE_DIRECTIONS[phase]:
            signal = signals[direction]
            if signal.actual_state == requested_state:
                continue
            if signal.actual_state != previous:
                return self._degrade(junction, f"Unexpected {direction} signal state.")

            command = ControllerCommand(
                junction_id=junction.id,
                direction=direction,
                requested_state=requested_state,
                status="PENDING",
                created_at=now,
                expires_at=now + timedelta(seconds=ACK_TIMEOUT_SECONDS),
            )
            signal.desired_state = requested_state
            self.session.add(command)
            self.session.flush()
            self._log(
                junction.id,
                "SIGNAL_STATE_REQUESTED",
                f"{direction} requested {requested_state}.",
                command.command_id,
            )
            return self._result(junction, "COMMAND_PENDING", command.command_id)
        return self._result(junction, "AWAITING_NEXT_TICK")

    @staticmethod
    def _all_confirmed(signals: dict, phase: str | None, color: str) -> bool:
        return phase in PHASE_DIRECTIONS and all(
            signals[direction].actual_state == color for direction in PHASE_DIRECTIONS[phase]
        )

    @staticmethod
    def _all_red(signals: dict) -> bool:
        return all(signal.actual_state == "RED" for signal in signals.values())

    def _degrade(self, junction: Junction, reason: str) -> dict:
        junction.controller_status = "DEGRADED"
        self._log(junction.id, "TRAFFIC_CONTROL_FAILURE", reason)
        return self._result(junction, "DEGRADED")

    def _log(self, junction_id: str, event_type: str, description: str, command_id: str | None = None) -> None:
        self.session.add(
            AuditLog(
                junction_id=junction_id,
                event_type=event_type,
                description=description,
                details={"command_id": command_id},
            )
        )

    @staticmethod
    def _result(junction: Junction, status: str, command_id: str | None = None) -> dict:
        return {
            "junction_id": junction.id,
            "execution_status": status,
            "phase": junction.active_phase,
            "target_phase": junction.target_phase,
            "transition_stage": junction.transition_stage,
            "controller_status": junction.controller_status,
            "pending_command_id": command_id,
        }
