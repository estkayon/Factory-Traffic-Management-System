
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.entities.traffic import Direction


class ControllerAcknowledgementStatus(str, Enum):
    ACK = "ACK"
    NACK = "NACK"


class ControllerAcknowledgement(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    command_id: str = Field(min_length=1)
    junction_id: str = Field(min_length=1)
    status: ControllerAcknowledgementStatus
    actual_state: str | None = None


class ControllerCommandResult(BaseModel):
    command_id: str
    junction_id: str
    direction: Direction
    requested_state: str
    status: str
