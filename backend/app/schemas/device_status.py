
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.entities.traffic import Direction


class DeviceType(str, Enum):
    SIGNAL_CONTROLLER = "SIGNAL_CONTROLLER"
    TRAFFIC_SIGNAL = "TRAFFIC_SIGNAL"
    VEHICLE_SENSOR = "VEHICLE_SENSOR"


class DeviceHealth(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"


class DeviceStatusRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    junction_id: str = Field(min_length=1, max_length=50)
    device_type: DeviceType
    direction: Direction | None = None
    status: DeviceHealth

    @model_validator(mode="after")
    def validate_device(self):
        if self.device_type == DeviceType.SIGNAL_CONTROLLER:
            if self.direction is not None:
                raise ValueError(
                    "The junction controller must not specify a direction."
                )
        elif self.direction is None:
            raise ValueError(
                "A direction is required for signals and sensors."
            )

        return self


class DeviceStatusResponse(BaseModel):
    junction_id: str
    device_type: DeviceType
    direction: Direction | None
    status: DeviceHealth
    controller_status: str
    message: str
