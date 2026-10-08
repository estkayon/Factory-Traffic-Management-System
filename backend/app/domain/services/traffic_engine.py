
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.app.domain.entities.traffic import (
    Phase,
    TrafficState,
    TransitionStage,
)


@dataclass(frozen=True)
class TrafficTiming:
    green_seconds: int = 30
    yellow_seconds: int = 5
    all_red_seconds: int = 2


class TrafficEngine:
    """
    Deterministic traffic signal state machine.

    This engine calculates desired signal transitions.
    It does not communicate with physical controllers.
    """

    def __init__(self, timing: TrafficTiming | None = None):
        self.timing = timing or TrafficTiming()

    def request_phase(
        self,
        state: TrafficState,
        target: Phase,
        now: datetime,
    ) -> TrafficState:
        """Start a safe transition to the requested traffic phase."""
        if target == Phase.ALL_RED:
            raise ValueError("ALL_RED is not a selectable green phase.")

        if state.stage != TransitionStage.IDLE:
            raise ValueError("A signal transition is already active.")

        if state.phase == target:
            return state

        if state.phase == Phase.ALL_RED:
            return TrafficState(
                phase=Phase.ALL_RED,
                stage=TransitionStage.ALL_RED,
                target_phase=target,
                stage_started_at=now,
                phase_started_at=None,
            )

        return TrafficState(
            phase=state.phase,
            stage=TransitionStage.YELLOW,
            target_phase=target,
            stage_started_at=now,
            phase_started_at=state.phase_started_at,
        )

    def advance(
        self,
        state: TrafficState,
        now: datetime,
    ) -> TrafficState:
        """Advance the state machine when its timer has expired."""
        if state.stage == TransitionStage.IDLE:
            return state

        if state.stage_started_at is None:
            raise ValueError("Transition start timestamp is missing.")

        if state.target_phase is None:
            raise ValueError("Transition target phase is missing.")

        elapsed = (now - state.stage_started_at).total_seconds()

        if state.stage == TransitionStage.YELLOW:
            if elapsed < self.timing.yellow_seconds:
                return state

            all_red_started = state.stage_started_at + timedelta(
                seconds=self.timing.yellow_seconds
            )

            next_state = TrafficState(
                phase=Phase.ALL_RED,
                stage=TransitionStage.ALL_RED,
                target_phase=state.target_phase,
                stage_started_at=all_red_started,
            )

            return self.advance(next_state, now)

        if state.stage == TransitionStage.ALL_RED:
            if elapsed < self.timing.all_red_seconds:
                return state

            green_started = state.stage_started_at + timedelta(
                seconds=self.timing.all_red_seconds
            )

            return TrafficState(
                phase=state.target_phase,
                stage=TransitionStage.IDLE,
                target_phase=None,
                stage_started_at=None,
                phase_started_at=green_started,
            )

        raise ValueError(f"Unsupported transition stage: {state.stage}")

    def green_duration_completed(
        self,
        state: TrafficState,
        now: datetime,
    ) -> bool:
        """Check whether the normal green duration has elapsed."""
        if (
            state.stage != TransitionStage.IDLE
            or state.phase == Phase.ALL_RED
            or state.phase_started_at is None
        ):
            return False

        elapsed = (now - state.phase_started_at).total_seconds()
        return elapsed >= self.timing.green_seconds
