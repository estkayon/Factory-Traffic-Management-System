
from dataclasses import dataclass
from enum import Enum

from .traffic import Phase, TrafficState


class ControlMode(str, Enum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True)
class ControlContext:
    """
    Operator preferences for a junction.

    Emergency mode is calculated from active emergency
    vehicles and is not directly set by the operator.
    """

    manual_phase: Phase | None = None


@dataclass(frozen=True)
class ControlDecision:
    """Result of one traffic-control evaluation."""

    state: TrafficState
    mode: ControlMode
    selected_phase: Phase | None
    reason: str
