import asyncio

from PySide6.QtCore import QThread, Signal

from .ai import Intelligence
from .runtime import Runtime


class Worker(QThread):
    ready = Signal()
    completed = Signal(str, object, str)

    def __init__(self, runtime: Runtime):
        super().__init__()
        self.runtime = runtime
        self.loop = None

    def run(self):
        asyncio.run(self.serve(), loop_factory=asyncio.SelectorEventLoop)

    async def serve(self):
        self.exit_event = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.update_lock = asyncio.Lock()
        self.runtime_task = None
        self.start_runtime()
        self.ready.emit()
        try:
            if not self.isInterruptionRequested():
                await self.exit_event.wait()
        finally:
            await self.stop_runtime()
            self.loop = None

    def start_runtime(self):
        self.stop_event = asyncio.Event()
        self.intelligence = Intelligence(
            self.runtime.settings.gemini, self.runtime.queue, self.runtime.vault, self.runtime.namespace
        )
        self.runtime_task = asyncio.create_task(self.runtime.run(self.stop_event))
        self.runtime_task.add_done_callback(self.runtime_finished)

    def runtime_finished(self, task):
        if not task.cancelled() and (error := task.exception()):
            self.completed.emit("Сбор данных", None, type(error).__name__)

    async def stop_runtime(self):
        self.stop_event.set()
        await asyncio.gather(self.runtime_task, return_exceptions=True)

    async def replace(self, settings):
        async with self.update_lock:
            await self.stop_runtime()
            self.runtime = Runtime(settings, self.runtime.queue, self.runtime.vault)
            self.start_runtime()
        return "Изменения применены."

    def submit(self, action, operation):
        if self.loop is None:
            self.completed.emit(action, None, "Фоновый процесс ещё запускается.")
            return

        async def execute():
            try:
                result = await operation()
            except Exception as error:
                code = getattr(getattr(error, "response", None), "status_code", None)
                detail = f"HTTP {code}" if code else type(error).__name__
                self.completed.emit(action, None, detail)
            else:
                self.completed.emit(action, result, "")

        asyncio.run_coroutine_threadsafe(execute(), self.loop)

    def shutdown(self):
        self.requestInterruption()
        if self.loop:
            self.loop.call_soon_threadsafe(self.exit_event.set)
