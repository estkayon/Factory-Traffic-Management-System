
from datetime import datetime

from backend.app.domain.entities.traffic import (
    PHASE_DIRECTIONS,
    Phase,
    VehicleType,
    WaitingVehicle,
)


VEHICLE_WEIGHTS: dict[VehicleType, int] = {
    VehicleType.EMPLOYEE_VEHICLE: 1,
    VehicleType.FORKLIFT: 2,
    VehicleType.TRUCK: 4,
    VehicleType.EMERGENCY: 100,
}


class TrafficScheduler:
    """Select the next phase using a transparent scoring policy."""

    def score_phase(
        self,
        phase: Phase,
        vehicles: list[WaitingVehicle],
        now: datetime,
    ) -> float:
        if phase not in PHASE_DIRECTIONS:
            raise ValueError("Cannot score an inactive traffic phase.")

        matching = [
            vehicle
            for vehicle in vehicles
            if vehicle.direction in PHASE_DIRECTIONS[phase]
        ]

        if not matching:
            return 0.0

        priority_score = sum(
            VEHICLE_WEIGHTS[vehicle.vehicle_type]
            for vehicle in matching
        )

        queue_score = len(matching) * 2

        longest_wait = max(
            max(0.0, (now - vehicle.arrived_at).total_seconds())
            for vehicle in matching
        )

        waiting_score = longest_wait / 10

        # Strong additional score after two minutes.
        starvation_bonus = 50 if longest_wait >= 120 else 0

        return (
            priority_score
            + queue_score
            + waiting_score
            + starvation_bonus
        )

    def choose_phase(
        self,
        vehicles: list[WaitingVehicle],
        now: datetime,
        current_phase: Phase = Phase.ALL_RED,
    ) -> Phase | None:
        """Return the highest-scoring phase, or None if queues are empty."""
        scores = {
            phase: self.score_phase(phase, vehicles, now)
            for phase in PHASE_DIRECTIONS
        }

        highest_score = max(scores.values(), default=0)

        if highest_score <= 0:
            return None

        # Avoid unnecessary phase switching on equal scores.
        if current_phase in scores:
            if scores[current_phase] == highest_score:
                return current_phase

        return max(scores, key=scores.get)
