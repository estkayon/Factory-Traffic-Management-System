
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.domain.entities.traffic import (
    Direction,
    Phase,
    SignalColor,
    TrafficState,
    TransitionStage,
    VehicleType,
    WaitingVehicle,
)
from backend.app.domain.services.traffic_engine import TrafficEngine
from backend.app.domain.services.traffic_scheduler import TrafficScheduler


NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


def test_initial_signals_are_all_red():
    state = TrafficState()

    assert all(
        signal == SignalColor.RED
        for signal in state.signal_states().values()
    )


def test_initial_phase_request_starts_with_all_red():
    engine = TrafficEngine()

    state = engine.request_phase(
        TrafficState(),
        Phase.NORTH_SOUTH,
        NOW,
    )

    assert state.stage == TransitionStage.ALL_RED
    assert all(
        signal == SignalColor.RED
        for signal in state.signal_states().values()
    )


def test_safe_phase_transition():
    engine = TrafficEngine()

    state = TrafficState(
        phase=Phase.NORTH_SOUTH,
        phase_started_at=NOW - timedelta(seconds=30),
    )

    state = engine.request_phase(state, Phase.EAST_WEST, NOW)

    assert state.stage == TransitionStage.YELLOW
    assert state.signal_states()[Direction.NORTH] == SignalColor.YELLOW
    assert state.signal_states()[Direction.EAST] == SignalColor.RED

    state = engine.advance(state, NOW + timedelta(seconds=5))

    assert state.phase == Phase.ALL_RED
    assert all(
        signal == SignalColor.RED
        for signal in state.signal_states().values()
    )

    state = engine.advance(state, NOW + timedelta(seconds=7))

    assert state.phase == Phase.EAST_WEST
    assert state.stage == TransitionStage.IDLE
    assert state.signal_states()[Direction.EAST] == SignalColor.GREEN
    assert state.signal_states()[Direction.NORTH] == SignalColor.RED


def test_phase_switch_never_creates_conflicting_green():
    engine = TrafficEngine()

    state = TrafficState(
        phase=Phase.NORTH_SOUTH,
        phase_started_at=NOW,
    )

    state = engine.request_phase(state, Phase.EAST_WEST, NOW)

    for second in range(12):
        state = engine.advance(
            state,
            NOW + timedelta(seconds=second),
        )

        signals = state.signal_states()

        ns_green = (
            signals[Direction.NORTH] == SignalColor.GREEN
            or signals[Direction.SOUTH] == SignalColor.GREEN
        )

        ew_green = (
            signals[Direction.EAST] == SignalColor.GREEN
            or signals[Direction.WEST] == SignalColor.GREEN
        )

        assert not (ns_green and ew_green)


def test_invalid_phase_request_is_rejected():
    engine = TrafficEngine()

    with pytest.raises(ValueError):
        engine.request_phase(
            TrafficState(),
            Phase.ALL_RED,
            NOW,
        )


def test_scheduler_prioritizes_emergency_vehicle():
    scheduler = TrafficScheduler()

    vehicles = [
        WaitingVehicle(
            vehicle_id="VH-1",
            direction=Direction.NORTH,
            vehicle_type=VehicleType.EMPLOYEE_VEHICLE,
            arrived_at=NOW,
        ),
        WaitingVehicle(
            vehicle_id="VH-2",
            direction=Direction.EAST,
            vehicle_type=VehicleType.EMERGENCY,
            arrived_at=NOW,
        ),
    ]

    selected = scheduler.choose_phase(
        vehicles=vehicles,
        now=NOW,
    )

    assert selected == Phase.EAST_WEST


def test_scheduler_returns_none_for_empty_queue():
    scheduler = TrafficScheduler()

    assert scheduler.choose_phase([], NOW) is None


def test_green_duration():
    engine = TrafficEngine()

    state = TrafficState(
        phase=Phase.NORTH_SOUTH,
        phase_started_at=NOW,
    )

    assert not engine.green_duration_completed(
        state, NOW + timedelta(seconds=29)
    )

    assert engine.green_duration_completed(
        state, NOW + timedelta(seconds=30)
    )
