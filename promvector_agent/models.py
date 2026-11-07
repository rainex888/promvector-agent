from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StringConstraints,
    field_validator,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]
Text = Annotated[str, StringConstraints(max_length=4096, pattern=r"^[^\x00]*$")]
Scalar = bool | int | FiniteFloat | Text
SourceKind = Literal["simulator", "files", "sqlite", "postgresql", "opc_ua", "modbus_tcp"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class Metric(StrictModel):
    value: Scalar
    unit: Annotated[str, StringConstraints(max_length=32, pattern=r"^[^\x00]*$")] | None = None


class Sample(StrictModel):
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    metrics: dict[Identifier, Metric] = Field(min_length=1, max_length=100)
    source_metadata: dict[Text, Scalar] = Field(default_factory=dict, max_length=256)
    quality: Literal["good", "uncertain", "bad"] = "good"
    event_key: str | None = Field(default=None, exclude=True)

    @field_validator("timestamp")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class Telemetry(Sample):
    schema_version: Literal["1.0"] = "1.0"
    message_id: UUID
    enterprise_id: Identifier
    agent_id: UUID
    equipment_id: Identifier
    source_id: Identifier
    source: SourceKind
    simulated: bool

    @classmethod
    def normalize(
        cls,
        sample: Sample,
        *,
        enterprise_id: str,
        agent_id: UUID,
        equipment_id: str,
        source_id: str,
        source: SourceKind,
        destination: str = "",
    ) -> "Telemetry":
        message_id = uuid4()
        if sample.event_key is not None:
            message_id = uuid5(
                agent_id, f"{destination}/{enterprise_id}/{source_id}/{equipment_id}/{sample.event_key}"
            )
        return cls(
            **sample.model_dump(),
            message_id=message_id,
            enterprise_id=enterprise_id,
            agent_id=agent_id,
            equipment_id=equipment_id,
            source_id=source_id,
            source=source,
            simulated=source == "simulator",
        )
