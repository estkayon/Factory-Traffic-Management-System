
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.database.models import (
    Junction,
    SignalState,
    Vehicle,
)
from backend.app.infrastructure.database.session import get_db


router = APIRouter(prefix="/api/junctions", tags=["Junctions"])

DIRECTIONS = ("NORTH", "SOUTH", "EAST", "WEST")


@router.get("")
def list_junctions(db: Session = Depends(get_db)):
    junctions = db.scalars(select(Junction)).all()

    return [
        {
            "id": junction.id,
            "name": junction.name,
            "mode": junction.mode,
        }
        for junction in junctions
    ]


@router.get("/{junction_id}/status")
def get_junction_status(
    junction_id: str,
    db: Session = Depends(get_db),
):
    junction = db.get(Junction, junction_id)

    if junction is None:
        raise HTTPException(
            status_code=404,
            detail="Junction not found.",
        )

    queue_rows = db.execute(
        select(Vehicle.direction, func.count())
        .where(
            Vehicle.junction_id == junction_id,
            Vehicle.is_waiting.is_(True),
        )
        .group_by(Vehicle.direction)
    ).all()

    queues = {direction: 0 for direction in DIRECTIONS}

    for direction, count in queue_rows:
        queues[direction] = count

    signals = db.scalars(
        select(SignalState).where(
            SignalState.junction_id == junction_id
        )
    ).all()

    return {
        "junction_id": junction.id,
        "mode": junction.mode,
        "phase": junction.active_phase,
        "transition_stage": junction.transition_stage,
        "controller_status": junction.controller_status,
        "desired_signals": {
            signal.direction: signal.desired_state
            for signal in signals
        },
        "actual_signals": {
            signal.direction: signal.actual_state
            for signal in signals
        },
        "queues": queues,
        "emergency_active": junction.mode == "EMERGENCY",
    }
