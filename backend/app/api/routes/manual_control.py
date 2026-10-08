
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from backend.app.domain.entities.traffic import Direction
from backend.app.domain.services.manual_control_service import (
    ManualControlError,
    ManualControlService,
)
from backend.app.infrastructure.database.session import get_db


router = APIRouter(
    prefix="/api/junctions",
    tags=["Manual Traffic Control"],
)


class ManualCommandRequest(BaseModel):
    command: Literal[
        "MANUAL_GREEN_REQUEST",
        "RETURN_TO_AUTOMATIC",
    ]
    direction: Direction | None = None

    @model_validator(mode="after")
    def validate_command(self):
        if (
            self.command == "MANUAL_GREEN_REQUEST"
            and self.direction is None
        ):
            raise ValueError(
                "Direction is required for manual GREEN requests."
            )

        if (
            self.command == "RETURN_TO_AUTOMATIC"
            and self.direction is not None
        ):
            raise ValueError(
                "Direction must be omitted when returning to automatic."
            )

        return self


@router.post("/{junction_id}/commands")
def execute_manual_command(
    junction_id: str,
    payload: ManualCommandRequest,
    db: Session = Depends(get_db),
):
    """Request manual traffic control without bypassing safety logic."""

    service = ManualControlService(db)

    try:
        return service.execute(
            junction_id=junction_id,
            command=payload.command,
            direction=(
                payload.direction.value
                if payload.direction is not None
                else None
            ),
        )
    except ManualControlError as exc:
        db.rollback()

        raise HTTPException(
            status_code=(
                404 if str(exc) == "Junction not found." else 409
            ),
            detail=str(exc),
        )
