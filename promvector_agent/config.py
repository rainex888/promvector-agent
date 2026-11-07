import json
import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from platformdirs import user_data_path
from pydantic import Field, field_validator, model_validator

from .models import Identifier, StrictModel

DATA_DIR = user_data_path("PromVector Agent", "PromVector")


class Source(StrictModel):
    id: Identifier
    equipment_id: Identifier
    equipment_name: str = Field(default="", max_length=100)
    enabled: bool = False
    authorized: bool = False
    interval: float = Field(default=2, ge=0.1, le=86400)

    @model_validator(mode="after")
    def require_authorization(self):
        if self.enabled and not self.authorized:
            raise ValueError("Enabled sources require explicit authorization")
        return self


class SimulatorSource(Source):
    kind: Literal["simulator"] = "simulator"


class Column(StrictModel):
    name: Identifier
    unit: str | None = None
    data_type: Literal["auto", "number", "integer", "boolean", "text"] = "auto"


class TableSource(Source):
    columns: dict[str, Column] = Field(min_length=1, max_length=100)
    timestamp_column: str | None = None

    @model_validator(mode="after")
    def distinct_metrics(self):
        if len({column.name for column in self.columns.values()}) != len(self.columns):
            raise ValueError("Metric names must be unique within a source")
        return self


class FileSource(TableSource):
    kind: Literal["files"] = "files"
    path: Path


class DatabaseSource(TableSource):
    query: str = Field(min_length=1, max_length=20_000)
    timestamp_column: str
    row_id_column: str
    row_limit: int = Field(default=1000, ge=1, le=10_000)


class SqliteSource(DatabaseSource):
    kind: Literal["sqlite"] = "sqlite"
    path: Path


class PostgresSource(DatabaseSource):
    kind: Literal["postgresql"] = "postgresql"


class OpcNode(StrictModel):
    node: str = Field(min_length=1)
    unit: str | None = None


class OpcSource(Source):
    kind: Literal["opc_ua"] = "opc_ua"
    endpoint: str
    nodes: dict[Identifier, OpcNode] = Field(min_length=1, max_length=100)
    subscribe: bool = True
    client_cert: Path | None = None
    client_key: Path | None = None
    server_cert: Path | None = None
    username: str | None = None

    @model_validator(mode="after")
    def valid_connection(self):
        endpoint = urlsplit(self.endpoint)
        if endpoint.scheme != "opc.tcp" or not endpoint.hostname or endpoint.username or endpoint.password:
            raise ValueError("Use an opc.tcp endpoint without embedded credentials")
        files = (self.client_cert, self.client_key, self.server_cert)
        if (self.username or any(files)) and not all(files):
            raise ValueError("Secure OPC UA requires client certificate, key and pinned server certificate")
        if len({node.node for node in self.nodes.values()}) != len(self.nodes):
            raise ValueError("OPC UA Node IDs must be unique")
        return self


class Register(StrictModel):
    address: int = Field(ge=0, le=65535)
    table: Literal["holding", "input"] = "holding"
    data_type: Literal["INT16", "UINT16", "INT32", "UINT32", "INT64", "UINT64", "FLOAT32", "FLOAT64"] = (
        "UINT16"
    )
    word_order: Literal["big", "little"] = "big"
    unit: str | None = None


class ModbusSource(Source):
    kind: Literal["modbus_tcp"] = "modbus_tcp"
    host: str = Field(min_length=1)
    port: int = Field(default=502, ge=1, le=65535)
    device_id: int = Field(default=1, ge=1, le=247)
    registers: dict[Identifier, Register] = Field(min_length=1, max_length=100)

    @field_validator("registers")
    @classmethod
    def address_range(cls, registers):
        if any(reg.address + int(reg.data_type[-2:]) // 16 > 65536 for reg in registers.values()):
            raise ValueError("Register value extends past address 65535")
        return registers


SourceConfig = Annotated[
    SimulatorSource | FileSource | SqliteSource | PostgresSource | OpcSource | ModbusSource,
    Field(discriminator="kind"),
]


class Backend(StrictModel):
    url: str = ""
    interval: float = Field(default=2, ge=0.2, le=3600)
    heartbeat_interval: float = Field(default=30, ge=1, le=86400)
    batch_size: int = Field(default=100, ge=1, le=500)
    connect_timeout: float = Field(default=5, gt=0, le=60)
    request_timeout: float = Field(default=15, gt=0, le=120)
    ca_file: Path | None = None
    send_simulated: bool = False

    @field_validator("url")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value:
            return value
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("Backend URL must be an HTTPS origin without credentials")
        return value.rstrip("/")


class Gemini(StrictModel):
    enabled: bool = False
    authorize_metadata: bool = False
    model: str = ""
    authorize_measurements: bool = False

    @model_validator(mode="after")
    def require_model(self):
        if self.enabled and (not self.model.strip() or not self.authorize_metadata):
            raise ValueError("Enable Gemini only with a model and explicit metadata authorization")
        return self


class Settings(StrictModel):
    enterprise_id: Identifier | None = None
    agent_id: UUID = Field(default_factory=uuid4)
    backend: Backend = Field(default_factory=Backend)
    gemini: Gemini = Field(default_factory=Gemini)
    theme: Literal["dark", "light", "system"] = "system"
    background: bool = False
    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_sources(self):
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique")
        return self


def prepare_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def save_settings(path: Path, settings: Settings) -> None:
    prepare_directory(path.parent)
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(settings.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_settings(path: Path) -> Settings:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = Settings.model_validate(data)
        if "agent_id" not in data:
            save_settings(path, settings)
        return settings
    settings = Settings()
    save_settings(path, settings)
    return settings
