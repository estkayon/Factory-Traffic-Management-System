
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.domain.entities.traffic import (
    Direction,
    VehicleType,
)


class SensorEventType(str, Enum):
    VEHICLE_ARRIVED = "VEHICLE_ARRIVED"
    VEHICLE_CLEARED = "VEHICLE_CLEARED"


class SensorEventRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=100)
    junction_id: str = Field(min_length=1, max_length=50)
    direction: Direction
    event_type: SensorEventType
    vehicle_id: str = Field(min_length=1, max_length=100)
    vehicle_type: VehicleType | None = None
    sequence_no: int = Field(ge=0)
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone.")
        return value.astimezone(timezone.utc)

    @field_validator("vehicle_type")
    @classmethod
    def validate_vehicle_type(cls, value):
        return value


class SensorEventResponse(BaseModel):
    event_id: str
    status: str
    message: str
    queue_size: int | None = None
    emergency_active: bool = False
