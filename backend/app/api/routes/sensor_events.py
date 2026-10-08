
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.domain.services.vehicle_event_service import (
    EventIdConflictError,
    UnknownJunctionError,
    VehicleEventService,
)
from backend.app.infrastructure.database.session import get_db
from backend.app.schemas.sensor_event import (
    SensorEventRequest,
    SensorEventResponse,
)


router = APIRouter(prefix="/api/sensor-events", tags=["Sensor Events"])


@router.post("", response_model=SensorEventResponse)
def submit_sensor_event(
    payload: SensorEventRequest,
    db: Session = Depends(get_db),
):
    """Process a vehicle arrival or clearance event."""
    service = VehicleEventService(db)

    try:
        return service.process(payload)
    except UnknownJunctionError:
        db.rollback()
        raise HTTPException(
            status_code=404,
            detail="Junction not found.",
        )
    except EventIdConflictError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Event ID already used with a different payload.",
        )
