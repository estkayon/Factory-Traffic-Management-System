
from datetime import datetime, timedelta, timezone

from backend.app.domain.entities.control import (
    ControlContext,
    ControlMode,
)
from backend.app.domain.entities.traffic import (
    Direction,
    Phase,
    SignalColor,
    TrafficState,
    TransitionStage,
    VehicleType,
    WaitingVehicle,
)
from backend.app.domain.services.junction_orchestrator import (
    JunctionOrchestrator,
)


NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)


def vehicle(
    vehicle_id: str,
    direction: Direction,
    vehicle_type: VehicleType,
    wait_seconds: int = 0,
) -> WaitingVehicle:
    return WaitingVehicle(
        vehicle_id=vehicle_id,
        direction=direction,
        vehicle_type=vehicle_type,
        arrived_at=NOW - timedelta(seconds=wait_seconds),
    )


def active_ns_state() -> TrafficState:
    return TrafficState(
        phase=Phase.NORTH_SOUTH,
        phase_started_at=NOW - timedelta(seconds=40),
    )


def test_automatic_selects_waiting_phase():
    controller = JunctionOrchestrator()

    result = controller.evaluate(
        state=TrafficState(),
        context=ControlContext(),
        vehicles=[
            vehicle("V1", Direction.EAST, VehicleType.TRUCK),
        ],
        now=NOW,
    )

    assert result.mode == ControlMode.AUTOMATIC
    assert result.selected_phase == Phase.EAST_WEST
    assert result.state.stage == TransitionStage.ALL_RED


def test_manual_control_overrides_normal_traffic():
    controller = JunctionOrchestrator()

    result = controller.evaluate(
        state=active_ns_state(),
        context=ControlContext(manual_phase=Phase.EAST_WEST),
        vehicles=[
            vehicle("V1", Direction.NORTH, VehicleType.TRUCK),
        ],
        now=NOW,
    )

    assert result.mode == ControlMode.MANUAL
    assert result.selected_phase == Phase.EAST_WEST
    assert result.state.stage == TransitionStage.YELLOW


def test_emergency_overrides_manual_control():
    controller = JunctionOrchestrator()

    result = controller.evaluate(
        state=active_ns_state(),
        context=ControlContext(manual_phase=Phase.NORTH_SOUTH),
        vehicles=[
            vehicle("E1", Direction.EAST, VehicleType.EMERGENCY),
        ],
        now=NOW,
    )

    assert result.mode == ControlMode.EMERGENCY
    assert result.state.stage == TransitionStage.YELLOW
    assert result.state.signal_states()[Direction.EAST] == SignalColor.RED


def test_automatic_respects_minimum_green_duration():
    controller = JunctionOrchestrator()

    state = TrafficState(
        phase=Phase.NORTH_SOUTH,
        phase_started_at=NOW - timedelta(seconds=10),
    )

    result = controller.evaluate(
        state=state,
        context=ControlContext(),
        vehicles=[
            vehicle("V1", Direction.EAST, VehicleType.TRUCK),
        ],
        now=NOW,
    )

    assert result.state.phase == Phase.NORTH_SOUTH
    assert result.state.stage == TransitionStage.IDLE


def test_new_request_does_not_interrupt_yellow():
    controller = JunctionOrchestrator()

    state = controller.engine.request_phase(
        active_ns_state(),
        Phase.EAST_WEST,
        NOW,
    )

    result = controller.evaluate(
        state=state,
        context=ControlContext(manual_phase=Phase.NORTH_SOUTH),
        vehicles=[],
        now=NOW + timedelta(seconds=2),
    )

    assert result.state.stage == TransitionStage.YELLOW
    assert result.state.target_phase == Phase.EAST_WEST


def test_conflicting_emergencies_choose_oldest():
    controller = JunctionOrchestrator()

    result = controller.evaluate(
        state=TrafficState(),
        context=ControlContext(),
        vehicles=[
            vehicle(
                "E2", Direction.EAST,
                VehicleType.EMERGENCY, wait_seconds=5
            ),
            vehicle(
                "E1", Direction.NORTH,
                VehicleType.EMERGENCY, wait_seconds=20
            ),
        ],
        now=NOW,
    )

    assert result.mode == ControlMode.EMERGENCY
    assert result.selected_phase == Phase.NORTH_SOUTH


def test_manual_mode_restored_after_emergency_clears():
    controller = JunctionOrchestrator()
    context = ControlContext(manual_phase=Phase.NORTH_SOUTH)

    during_emergency = controller.evaluate(
        state=TrafficState(),
        context=context,
        vehicles=[
            vehicle("E1", Direction.EAST, VehicleType.EMERGENCY)
        ],
        now=NOW,
    )

    after_emergency = controller.evaluate(
        state=TrafficState(),
        context=context,
        vehicles=[],
        now=NOW + timedelta(seconds=10),
    )

    assert during_emergency.mode == ControlMode.EMERGENCY
    assert after_emergency.mode == ControlMode.MANUAL
    assert after_emergency.selected_phase == Phase.NORTH_SOUTH
