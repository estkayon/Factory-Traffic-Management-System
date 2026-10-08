
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.infrastructure.controllers.command_service import (
    ControllerCommandError,
    ControllerCommandService,
)
from backend.app.infrastructure.controllers.recovery_service import (
    ControllerRecoveryService,
)
from backend.app.infrastructure.database.session import get_db
from backend.app.schemas.controller import ControllerAcknowledgement


router = APIRouter(
    prefix="/api/controller-events",
    tags=["Controller Simulation"],
)


class ReconciliationRequest(BaseModel):
    junction_id: str = Field(min_length=1)
    confirmed_state: str


@router.post("")
def acknowledge_controller_command(
    payload: ControllerAcknowledgement,
    db: Session = Depends(get_db),
):
    """Record a controller acknowledgement."""

    service = ControllerCommandService(db)

    try:
        command = service.acknowledge(
            command_id=payload.command_id,
            junction_id=payload.junction_id,
            status=payload.status.value,
            actual_state=payload.actual_state,
        )

        return {
            "command_id": command.command_id,
            "status": command.status,
            "junction_id": command.junction_id,
        }

    except ControllerCommandError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )


@router.post("/reconcile")
def reconcile_controller(
    payload: ReconciliationRequest,
    db: Session = Depends(get_db),
):
    """Simulate confirmation that the junction is ALL_RED."""

    if payload.confirmed_state != "ALL_RED":
        raise HTTPException(
            status_code=422,
            detail="Only ALL_RED reconciliation is supported.",
        )

    service = ControllerRecoveryService(db)

    try:
        service.reconcile_all_red(payload.junction_id)

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        )

    return {
        "junction_id": payload.junction_id,
        "controller_status": "ONLINE",
        "confirmed_state": "ALL_RED",
        "message": "Simulator reconciliation completed.",
    }
