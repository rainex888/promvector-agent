from datetime import UTC, datetime

import httpx
import pytest

from promvector_agent.communication import BackendClient, Communication
from promvector_agent.config import Backend, Settings
from promvector_agent.models import Metric, Sample, Telemetry
from promvector_agent.storage import Queue


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


async def test_offline_restart_reconnect_and_partial_ack(tmp_path):
    settings = Settings(enterprise_id="site", backend=Backend(url="https://example.test"))
    queue = Queue(tmp_path / "queue.sqlite")
    reading = Sample(timestamp=datetime.now(UTC), metrics={"rpm": Metric(value=42)})
    messages = [
        Telemetry.normalize(
            reading,
            enterprise_id="site",
            agent_id=settings.agent_id,
            equipment_id="pump",
            source_id="meter",
            source="opc_ua",
        )
        for _ in range(2)
    ]
    scope = Communication(settings, queue, MemoryVault()).scope
    queue.ingest(messages, destination=scope)
    call = 0

    def response(request):
        nonlocal call
        assert request.headers["Authorization"] == "Bearer credential"
        assert request.url.path == "/api/v1/telemetry/batch"
        for message in messages:
            assert str(message.message_id).encode() in request.content
        call += 1
        if call == 1:
            raise httpx.ConnectError("offline")
        if call == 2:
            return httpx.Response(401)
        receipt = {"message_id": str(messages[0].message_id), "status": "duplicate"}
        if call == 3:
            return httpx.Response(200, json={"receipts": [receipt]})
        return httpx.Response(
            200,
            json={
                "receipts": [
                    receipt,
                    {
                        "message_id": str(messages[1].message_id),
                        "status": "rejected",
                        "code": "unknown_sensor",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(response), base_url=settings.backend.url
    ) as http:
        vault = MemoryVault()
        backend = BackendClient(http, settings)
        agent = Communication(settings, queue, vault)
        with pytest.raises(httpx.ConnectError):
            await agent.send_once(backend, "credential")
        assert queue.counts() == {"pending": 2}
        queue = Queue(queue.path)
        agent = Communication(settings, queue, vault)
        with pytest.raises(httpx.HTTPStatusError) as revoked:
            await agent.send_once(backend, "credential")
        agent.failure(revoked.value)
        assert queue.state()["auth"] == "denied"
        with pytest.raises(ValueError, match="acknowledgment"):
            await agent.send_once(backend, "credential")
        assert queue.counts() == {"pending": 2}
        await agent.send_once(backend, "credential")
        assert queue.counts() == {"delivered": 1, "blocked": 1}
        assert queue.requeue_blocked("site", str(settings.agent_id), scope) == 1
        assert queue.pending("site", str(settings.agent_id), 5, False, scope) == [messages[1]]


async def test_exponential_backoff_and_reset(tmp_path, monkeypatch):
    settings = Settings(enterprise_id="site", backend=Backend(url="https://example.test"))
    vault = MemoryVault()
    queue = Queue(tmp_path / "queue.sqlite")
    agent = Communication(settings, queue, vault)
    vault.set(agent.token_key, "credential")
    delays = []
    attempts = 0

    class Finished(Exception):
        pass

    async def pause(delay):
        delays.append(delay)
        if len(delays) == 4:
            raise Finished

    async def work(_backend, token):
        nonlocal attempts
        assert token == "credential"
        attempts += 1
        if attempts <= 2:
            raise httpx.ConnectError("offline")

    monkeypatch.setattr("promvector_agent.communication.asyncio.sleep", pause)
    with pytest.raises(Finished):
        await agent.repeat(None, work, 2)
    assert delays == [4, 8, 2, 2]
    assert queue.state()["failed_requests"] == 2
