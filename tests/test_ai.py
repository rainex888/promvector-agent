import pytest
from test_communication import MemoryVault

from promvector_agent.ai import Advice, Intelligence, gemini_secret
from promvector_agent.config import Gemini
from promvector_agent.storage import Queue


async def test_ai_requires_consent_caches_and_leaves_queue_independent(tmp_path, monkeypatch):
    queue = Queue(tmp_path / "queue.sqlite")
    vault = MemoryVault()
    vault.set(gemini_secret("plant"), "credential")
    settings = Gemini(enabled=True, authorize_metadata=True, model="gemini-test")
    service = Intelligence(settings, queue, vault, "plant")
    calls = 0

    async def generate(key, mode, text):
        nonlocal calls
        assert key == "credential"
        calls += 1
        if text == "failure":
            raise TimeoutError
        return Advice(summary="Possible rotational speed", findings=[], anomalies=[], limitations=[])

    monkeypatch.setattr(service, "generate", generate)
    with pytest.raises(PermissionError):
        await service.analyze("metadata", "rpm", approved=False)
    with pytest.raises(PermissionError):
        await service.analyze("measurements", "1450", approved=True)
    result, cached = await service.analyze("metadata", "rpm", approved=True)
    assert not cached
    assert await service.analyze("metadata", "rpm", approved=True) == (result, True)
    assert calls == 1
    with pytest.raises(RuntimeError, match="TimeoutError"):
        await service.analyze("metadata", "failure", approved=True)
    assert queue.counts() == {}
    assert queue.state()["gemini_status"] == "error"
    assert len(queue.ai_history()) == 1
