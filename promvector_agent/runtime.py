import asyncio
import logging
import sqlite3

from .collectors.databases import Database
from .collectors.files import Files
from .collectors.modbus import Modbus
from .collectors.opcua import OpcUA
from .collectors.simulator import Simulator
from .communication import Communication
from .config import (
    FileSource,
    ModbusSource,
    OpcSource,
    PostgresSource,
    Settings,
    SimulatorSource,
    SqliteSource,
)
from .models import Telemetry
from .security import Vault
from .storage import Queue

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings, queue: Queue, vault: Vault):
        self.settings = settings
        self.queue = queue
        self.vault = vault
        self.namespace = f"{settings.enterprise_id or 'local'}/{settings.agent_id}"
        self.communication = Communication(settings, queue, vault)

    def collector(self, source):
        match source:
            case SimulatorSource():
                return Simulator()
            case FileSource():
                return Files(source, self.queue, self.communication.scope)
            case SqliteSource() | PostgresSource():
                return Database(source, self.vault, self.namespace)
            case ModbusSource():
                return Modbus(source)
            case OpcSource():
                return OpcUA(source, self.vault, self.namespace)
            case _:
                raise TypeError("Unsupported collector")

    async def test(self, source):
        if not source.authorized:
            raise ValueError("Confirm local authorization before connecting to the source")
        async with asyncio.timeout(35):
            await self.collector(source).test()

    def persist(self, source, collection):
        enterprise = self.settings.enterprise_id or "local"
        messages = (
            Telemetry.normalize(
                sample,
                enterprise_id=enterprise,
                agent_id=self.settings.agent_id,
                equipment_id=source.equipment_id,
                source_id=source.id,
                source=source.kind,
                destination=self.communication.scope,
            )
            for sample in collection.samples
        )
        return self.queue.ingest(messages, file_key=collection.file_key, destination=self.communication.scope)

    async def capture(self, source):
        collector = self.collector(source)
        delay = source.interval
        while True:
            try:
                async for collection in collector.collect():
                    await asyncio.to_thread(self.persist, source, collection)
                    self.queue.source_status(source.id, "collecting")
                self.queue.source_status(source.id, "connected")
            except Exception as exc:
                detail = type(exc).__name__
                if isinstance(exc, sqlite3.Error):
                    logger.error("Source %s could not complete a SQLite operation (%s)", source.id, detail)
                try:
                    previous = self.queue.sources().get(source.id, {})
                    self.queue.source_status(source.id, "error", detail)
                    if previous.get("status") != "error" or previous.get("detail") != detail:
                        self.queue.log("source_error", f"{source.id}: {detail}")
                except sqlite3.Error:
                    logger.error("Unable to persist source failure for %s", source.id)
                delay = min(max(source.interval, 60), max(1, delay * 2))
            else:
                delay = source.interval
            await asyncio.sleep(delay)

    async def run(self, stop: asyncio.Event):
        self.queue.log("runtime_start", "Local collection started")
        stopped = asyncio.create_task(stop.wait())
        tasks = [asyncio.create_task(self.communication.run())]
        try:
            for source in self.settings.sources:
                if source.enabled:
                    if self.settings.enterprise_id is None and not isinstance(source, SimulatorSource):
                        self.queue.source_status(
                            source.id, "setup_required", "Set enterprise ID before collection"
                        )
                        continue
                    self.queue.source_status(source.id, "starting")
                    tasks.append(asyncio.create_task(self.capture(source)))
                else:
                    self.queue.source_status(source.id, "disabled")
            done, _ = await asyncio.wait([stopped, *tasks[1:]], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        finally:
            for task in [stopped, *tasks]:
                task.cancel()
            await asyncio.gather(stopped, *tasks, return_exceptions=True)
            self.queue.log("runtime_stop", "Local collection stopped")
