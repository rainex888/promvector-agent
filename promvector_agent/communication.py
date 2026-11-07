import asyncio
import secrets
import ssl
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Literal
from uuid import UUID

import httpx
from pydantic import Field, ValidationError

from . import __version__
from .config import Backend, Settings
from .models import Identifier, StrictModel, Telemetry
from .security import Vault, secret_name
from .storage import Queue, now

API = "/api/v1"
Failure = httpx.HTTPError | ValueError | RuntimeError | OSError | TimeoutError


class Registration(StrictModel):
    enterprise_id: Identifier
    agent_id: UUID


class Receipt(StrictModel):
    message_id: UUID
    status: Literal["accepted", "duplicate", "rejected"]
    code: Identifier | None = None


class Acknowledgment(StrictModel):
    receipts: list[Receipt] = Field(min_length=1, max_length=500)


class Heartbeat(StrictModel):
    status: Literal["ok"]


class Configuration(StrictModel):
    enterprise_id: Identifier
    agent_id: UUID
    equipment_ids: list[Identifier]
    allow_simulated: bool


def client_options(settings: Backend) -> dict:
    context = ssl.create_default_context()
    if settings.ca_file:
        context.load_verify_locations(cafile=str(settings.ca_file))
    return {
        "base_url": settings.url,
        "verify": context,
        "trust_env": False,
        "follow_redirects": False,
        "timeout": httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout),
    }


class BackendClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        self.http = http
        self.settings = settings

    async def request(self, method: str, path: str, token: str, payload: dict | None = None):
        async with asyncio.timeout(self.settings.backend.request_timeout):
            response = await self.http.request(
                method, API + path, json=payload, headers={"Authorization": f"Bearer {token}"}
            )
        response.raise_for_status()
        return response

    async def register(self, enrollment: str, candidate: str) -> None:
        response = await self.request(
            "POST",
            "/agents/register",
            enrollment,
            {
                "enterprise_id": self.settings.enterprise_id,
                "agent_id": str(self.settings.agent_id),
                "agent_token": candidate,
                "version": __version__,
            },
        )
        result = Registration.model_validate_json(response.content)
        if result.enterprise_id != self.settings.enterprise_id or result.agent_id != self.settings.agent_id:
            raise ValueError("Registration identity mismatch")

    async def heartbeat(self, token: str, pending: int) -> None:
        response = await self.request(
            "POST",
            "/agents/heartbeat",
            token,
            {
                "version": __version__,
                "pending": pending,
            },
        )
        Heartbeat.model_validate_json(response.content)

    async def configuration(self, token: str) -> Configuration:
        response = await self.request("GET", "/agents/configuration", token)
        result = Configuration.model_validate_json(response.content)
        if result.enterprise_id != self.settings.enterprise_id or result.agent_id != self.settings.agent_id:
            raise ValueError("Configuration identity mismatch")
        return result

    async def send(self, token: str, messages: list[Telemetry]) -> list[Receipt]:
        response = await self.request(
            "POST",
            "/telemetry/batch",
            token,
            {
                "messages": [message.model_dump(mode="json") for message in messages],
            },
        )
        receipts = Acknowledgment.model_validate_json(response.content).receipts
        if len(receipts) != len(messages) or {r.message_id for r in receipts} != {
            m.message_id for m in messages
        }:
            raise ValueError("Batch acknowledgment did not match submitted message IDs")
        return receipts


class Communication:
    def __init__(self, settings: Settings, queue: Queue, vault: Vault):
        self.settings = settings
        self.queue = queue
        self.vault = vault
        self.enterprise = settings.enterprise_id
        self.agent = str(settings.agent_id)
        self.scope = secret_name(settings.backend.url, self.enterprise, self.agent)
        self.token_key = "backend:" + self.scope
        self.candidate_key = "enrollment:" + self.scope

    @asynccontextmanager
    async def connect(self):
        if not self.settings.backend.url or not self.enterprise:
            raise ValueError("Configure an enterprise and HTTPS backend origin first")
        async with httpx.AsyncClient(**client_options(self.settings.backend)) as http:
            yield BackendClient(http, self.settings)

    def token(self) -> str | None:
        return self.vault.get(self.token_key)

    async def register(self, enrollment: str) -> None:
        if not enrollment.strip():
            raise ValueError("An enrollment token is required")
        candidate = self.vault.get(self.candidate_key) or secrets.token_urlsafe(48)
        self.vault.set(self.candidate_key, candidate)
        async with self.connect() as backend:
            await backend.register(enrollment.strip(), candidate)
        self.vault.set(self.token_key, candidate)
        self.queue.set_state(scope=self.scope, auth="registered", backend="reachable")
        self.queue.log("registration", "Agent enrollment acknowledged")

    def failure(self, error: Failure) -> None:
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        detail = f"HTTP {status}" if status else type(error).__name__
        auth = "denied" if status in (401, 403) else self.queue.state().get("auth", "unknown")
        failures = self.queue.state().get("failed_requests", 0)
        self.queue.set_state(
            scope=self.scope,
            backend="reachable" if status else "error",
            auth=auth,
            last_error=detail,
            failed_requests=failures + 1,
        )
        self.queue.log("communication_error", detail)

    async def send_once(self, backend: BackendClient, token: str) -> None:
        config = self.settings.backend
        messages = self.queue.pending(
            self.enterprise, self.agent, config.batch_size, config.send_simulated, self.scope
        )
        if not messages:
            return
        started = monotonic()
        receipts = await backend.send(token, messages)
        self.queue.resolve(
            [str(r.message_id) for r in receipts if r.status != "rejected"],
            {str(r.message_id): r.code or "rejected" for r in receipts if r.status == "rejected"},
        )
        rejected = sum(r.status == "rejected" for r in receipts)
        if rejected:
            self.queue.log("telemetry_rejected", f"{rejected} message(s) retained in blocked queue")
        self.queue.set_state(
            scope=self.scope,
            backend="reachable",
            auth="registered",
            last_error=None,
            last_sync=now(),
            latency_ms=round((monotonic() - started) * 1000),
        )

    async def heartbeat_once(self, backend: BackendClient, token: str) -> None:
        await backend.heartbeat(token, self.queue.counts(self.scope).get("pending", 0))
        self.queue.set_state(scope=self.scope, backend="reachable", auth="registered", last_heartbeat=now())

    async def check(self) -> Configuration:
        token = self.token()
        if not token:
            raise ValueError("Register this agent first")
        async with self.connect() as backend:
            await self.heartbeat_once(backend, token)
            return await backend.configuration(token)

    async def run(self) -> None:
        if not self.settings.backend.url or not self.enterprise:
            return
        if self.queue.state().get("scope") != self.scope:
            self.queue.set_state(
                scope=self.scope,
                backend="unknown",
                auth="unknown",
                last_error=None,
                last_sync=None,
                last_heartbeat=None,
                latency_ms=None,
                failed_requests=0,
            )
        try:
            async with self.connect() as backend:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(self.repeat(backend, self.send_once, self.settings.backend.interval))
                    tasks.create_task(
                        self.repeat(
                            backend,
                            self.heartbeat_once,
                            self.settings.backend.heartbeat_interval,
                        )
                    )
        except (OSError, ValueError) as exc:
            self.failure(exc)

    async def repeat(
        self, backend: BackendClient, work: Callable[[BackendClient, str], Awaitable[None]], interval: float
    ) -> None:
        delay = interval
        while True:
            try:
                token = self.token()
                if token:
                    await work(backend, token)
                else:
                    self.queue.set_state(scope=self.scope, auth="unregistered")
            except (httpx.HTTPError, ValidationError, ValueError, RuntimeError, OSError, TimeoutError) as exc:
                self.failure(exc)
                delay = min(max(interval, 120), max(1, delay * 2))
            else:
                delay = interval
            await asyncio.sleep(delay)
