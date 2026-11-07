import json
import math
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Mapping
from uuid import UUID

from ..config import TableSource
from ..models import Metric, Sample


def converted(value, kind: str):
    if kind == "number":
        return float(value)
    if kind == "integer":
        if isinstance(value, str):
            return int(value)
        integer = int(value)
        if integer != value:
            raise ValueError("Integer metric contains a fractional value")
        return integer
    if kind == "boolean":
        values = {"true": True, "false": False, "1": True, "0": False}
        return values[str(value).lower()]
    if kind == "text":
        return str(value)
    if isinstance(value, Decimal):
        if not math.isfinite(value):
            raise ValueError("Non-finite decimal value")
        return str(value)
    if isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("Metric value must be scalar")


def sample_from_row(source: TableSource, row: Mapping, metadata: dict, *, identity: str | None = None):
    values = {}
    raw = {}
    for key, column in source.columns.items():
        value = row[key]
        if value is None or value == "":
            continue
        if type(value) not in (bool, int, float, str, Decimal):
            raise ValueError("Mapped column is not scalar")
        values[column.name] = Metric(value=converted(value, column.data_type), unit=column.unit)
        if type(values[column.name].value) is not type(value) or values[column.name].value != value:
            raw[f"raw:{key}"] = str(value) if isinstance(value, Decimal) else value

    kwargs = {}
    if source.timestamp_column:
        value = row[source.timestamp_column]
        if not isinstance(value, (str, datetime)) or not value:
            raise ValueError("The configured timestamp must be a timezone-aware ISO date")
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        kwargs["timestamp"] = value
        raw["original_timestamp"] = value.isoformat()

    return Sample(**kwargs, metrics=values, source_metadata={**metadata, **raw}, event_key=identity)


def row_key(identifier, sample: Sample) -> str:
    if type(identifier) not in (int, str, UUID):
        raise ValueError("Row identity must be a nonempty string, integer or UUID")
    if identifier == "":
        raise ValueError("Row identity must be nonempty")
    payload = json.dumps(
        [str(identifier), sample.model_dump(mode="json")], sort_keys=True, ensure_ascii=False
    )
    return sha256(payload.encode()).hexdigest()
