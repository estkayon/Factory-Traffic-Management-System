
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.infrastructure.controllers.device_status_service import (
    DeviceStatusError,
    DeviceStatusService,
)
from backend.app.infrastructure.database.session import get_db
from backend.app.schemas.device_status import (
    DeviceStatusRequest,
    DeviceStatusResponse,
)


router = APIRouter(
    prefix="/api/device-status",
    tags=["Device Monitoring"],
)


@router.post("", response_model=DeviceStatusResponse)
def update_device_status(
    payload: DeviceStatusRequest,
    db: Session = Depends(get_db),
):
    """Receive a simulated controller, signal, or sensor health event."""

    try:
        return DeviceStatusService(db).update(payload)
    except DeviceStatusError as exc:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
