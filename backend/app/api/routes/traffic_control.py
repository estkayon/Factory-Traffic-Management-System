
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.infrastructure.controllers.traffic_execution_service import (
    TrafficExecutionError,
    TrafficExecutionService,
)
from backend.app.infrastructure.database.session import get_db


router = APIRouter(
    prefix="/api/junctions",
    tags=["Traffic Control"],
)


@router.post("/{junction_id}/tick")
def execute_control_tick(
    junction_id: str,
    db: Session = Depends(get_db),
):
    """
    Advance automatic traffic execution by one step.

    Intended for simulation and controlled evaluation.
    """
    try:
        return TrafficExecutionService(db).tick(junction_id)
    except TrafficExecutionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404 if str(exc) == "Unknown junction." else 409,
            detail=str(exc),
        )
