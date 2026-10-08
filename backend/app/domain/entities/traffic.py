from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Direction(str, Enum):
    NORTH = "NORTH"
    SOUTH = "SOUTH"
    EAST = "EAST"
    WEST = "WEST"


class Phase(str, Enum):
    NORTH_SOUTH = "NORTH_SOUTH"
    EAST_WEST = "EAST_WEST"
    ALL_RED = "ALL_RED"


class SignalColor(str, Enum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


class TransitionStage(str, Enum):
    IDLE = "IDLE"
    YELLOW = "YELLOW"
    ALL_RED = "ALL_RED"


class VehicleType(str, Enum):
    EMPLOYEE_VEHICLE = "EMPLOYEE_VEHICLE"
    FORKLIFT = "FORKLIFT"
    TRUCK = "TRUCK"
    EMERGENCY = "EMERGENCY"


PHASE_DIRECTIONS: dict[Phase, frozenset[Direction]] = {
    Phase.NORTH_SOUTH: frozenset({
        Direction.NORTH,
        Direction.SOUTH,
    }),
    Phase.EAST_WEST: frozenset({
        Direction.EAST,
        Direction.WEST,
    }),
}


@dataclass(frozen=True)
class WaitingVehicle:
    vehicle_id: str
    direction: Direction
    vehicle_type: VehicleType
    arrived_at: datetime


@dataclass
class TrafficState:
    phase: Phase = Phase.ALL_RED
    stage: TransitionStage = TransitionStage.IDLE
    target_phase: Phase | None = None
    stage_started_at: datetime | None = None
    phase_started_at: datetime | None = None

    def signal_states(self) -> dict[Direction, SignalColor]:
        """Derive intended signal colors from the junction state."""
        states = {
            direction: SignalColor.RED
            for direction in Direction
        }

        if self.phase == Phase.ALL_RED:
            return states

        active_color = (
            SignalColor.YELLOW
            if self.stage == TransitionStage.YELLOW
            else SignalColor.GREEN
        )

        for direction in PHASE_DIRECTIONS[self.phase]:
            states[direction] = active_color

        return states
