
from datetime import datetime

from backend.app.domain.entities.control import (
    ControlContext,
    ControlDecision,
    ControlMode,
)
from backend.app.domain.entities.traffic import (
    PHASE_DIRECTIONS,
    Phase,
    TrafficState,
    TransitionStage,
    VehicleType,
    WaitingVehicle,
)
from backend.app.domain.services.traffic_engine import TrafficEngine
from backend.app.domain.services.traffic_scheduler import (
    TrafficScheduler,
)


class JunctionOrchestrator:
    """
    Coordinate traffic scheduling and control priorities.

    Priority:
        Emergency > Manual > Automatic

    This class calculates desired states only.
    Physical device commands belong to a separate adapter.
    """

    def __init__(
        self,
        engine: TrafficEngine | None = None,
        scheduler: TrafficScheduler | None = None,
    ):
        self.engine = engine or TrafficEngine()
        self.scheduler = scheduler or TrafficScheduler()

    def evaluate(
        self,
        state: TrafficState,
        context: ControlContext,
        vehicles: list[WaitingVehicle],
        now: datetime,
    ) -> ControlDecision:
        """Evaluate one junction control cycle."""

        mode, target, reason = self._select_target(
            context=context,
            vehicles=vehicles,
            now=now,
            current_phase=state.phase,
        )

        # Finish any transitions whose deadlines have passed.
        updated_state = self.engine.advance(state, now)

        # A transition must finish before another can begin.
        # A new emergency request remains represented by the
        # current vehicle list and will be reconsidered next tick.
        if updated_state.stage != TransitionStage.IDLE:
            return ControlDecision(
                state=updated_state,
                mode=mode,
                selected_phase=target,
                reason="Transition in progress; request pending.",
            )

        if target is None:
            return ControlDecision(
                state=updated_state,
                mode=mode,
                selected_phase=None,
                reason="No phase change requested.",
            )

        if updated_state.phase == target:
            return ControlDecision(
                state=updated_state,
                mode=mode,
                selected_phase=target,
                reason=reason,
            )

        # Manual and emergency requests can preempt the
        # normal green timer, but never the safety sequence.
        if mode == ControlMode.AUTOMATIC:
            if (
                updated_state.phase != Phase.ALL_RED
                and not self.engine.green_duration_completed(
                    updated_state, now
                )
            ):
                return ControlDecision(
                    state=updated_state,
                    mode=mode,
                    selected_phase=updated_state.phase,
                    reason="Minimum green duration not reached.",
                )

        next_state = self.engine.request_phase(
            updated_state,
            target,
            now,
        )

        return ControlDecision(
            state=next_state,
            mode=mode,
            selected_phase=target,
            reason=reason,
        )

    def _select_target(
        self,
        context: ControlContext,
        vehicles: list[WaitingVehicle],
        now: datetime,
        current_phase: Phase,
    ) -> tuple[ControlMode, Phase | None, str]:
        """Apply the configured control-priority policy."""

        emergency_vehicles = [
            vehicle
            for vehicle in vehicles
            if vehicle.vehicle_type == VehicleType.EMERGENCY
        ]

        if emergency_vehicles:
            # Oldest emergency gets priority when opposing
            # emergency requests conflict.
            oldest = min(
                emergency_vehicles,
                key=lambda vehicle: (
                    vehicle.arrived_at,
                    vehicle.vehicle_id,
                ),
            )

            for phase, directions in PHASE_DIRECTIONS.items():
                if oldest.direction in directions:
                    return (
                        ControlMode.EMERGENCY,
                        phase,
                        "Emergency vehicle priority.",
                    )

        if context.manual_phase is not None:
            if context.manual_phase == Phase.ALL_RED:
                raise ValueError(
                    "ALL_RED cannot be requested as manual GREEN."
                )

            return (
                ControlMode.MANUAL,
                context.manual_phase,
                "Administrator manual phase request.",
            )

        selected = self.scheduler.choose_phase(
            vehicles=vehicles,
            now=now,
            current_phase=current_phase,
        )

        return (
            ControlMode.AUTOMATIC,
            selected,
            "Automatic queue-based scheduling.",
        )
