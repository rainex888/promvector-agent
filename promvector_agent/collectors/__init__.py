"""Read-only connector interface for industrial sources and future SCADA/MES adapters."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from ..models import Sample


@dataclass(frozen=True)
class Collection:
    samples: list[Sample]
    file_key: str | None = None


class Collector(Protocol):
    def collect(self) -> AsyncIterator[Collection]: ...

    async def test(self) -> None: ...
