from datetime import UTC
from uuid import uuid4

import pytest
from pydantic import ValidationError

from promvector_agent.collectors.simulator import Simulator
from promvector_agent.config import Settings, load_settings
from promvector_agent.models import Metric, Sample, Telemetry
from promvector_agent.storage import Queue


def normalize(sample, agent, source="simulator"):
    return Telemetry.normalize(
        sample, enterprise_id="plant", agent_id=agent, equipment_id="motor", source_id="sensor", source=source
    )


async def test_simulator_and_restart(tmp_path):
    settings = load_settings(tmp_path / "settings.json")
    sample = (await anext(Simulator().collect())).samples[0]
    telemetry = normalize(sample, settings.agent_id)
    assert telemetry.simulated and len(telemetry.metrics) == 5
    path = tmp_path / "queue.sqlite"
    queue = Queue(path)
    assert queue.ingest([telemetry, telemetry]) == 1
    assert load_settings(tmp_path / "settings.json").agent_id == settings.agent_id
    restarted = Queue(path)
    assert restarted.pending("plant", str(settings.agent_id), 1, False) == []
    assert restarted.pending("plant", str(settings.agent_id), 1, True) == [telemetry]
    assert restarted.pending("other", str(settings.agent_id), 1, True) == []


def test_normalization_preserves_data_and_rejects_invalid():
    sample = Sample(timestamp="2026-11-07T18:30:00+05:00", metrics={"value": Metric(value=7)})
    result = normalize(sample, uuid4(), "opc_ua")
    assert result.timestamp.tzinfo is UTC and result.timestamp.hour == 13
    assert type(result.metrics["value"].value) is int and result.metrics["value"].unit is None
    assert not result.simulated
    with pytest.raises(ValidationError):
        Metric(value=float("nan"))
    with pytest.raises(ValidationError):
        Sample(timestamp="2026-11-07T18:30:00", metrics=sample.metrics)
    with pytest.raises(ValidationError):
        Settings(sources=[{"id": "x", "equipment_id": "m", "kind": "simulator", "enabled": True}])


def test_file_checkpoint_is_atomic(tmp_path):
    queue = Queue(tmp_path / "queue.sqlite")
    agent = uuid4()
    sample = Sample(metrics={"v": Metric(value=3)}, event_key="stable")
    message = normalize(sample, agent, "files")
    assert message.message_id == normalize(sample, agent, "files").message_id

    def interrupted():
        yield message
        raise OSError

    with pytest.raises(OSError):
        queue.ingest(interrupted(), file_key="file")
    assert not queue.seen_file("file")
    assert queue.counts() == {}
    assert queue.ingest([message], file_key="file") == 1
    assert queue.ingest([message], file_key="file") == 0


def test_queue_statistics_follow_retry_and_acknowledgment(tmp_path):
    queue = Queue(tmp_path / "queue.sqlite")
    agent = uuid4()
    sample = Sample(metrics={"rpm": Metric(value=42), "state": Metric(value="on")})
    first = normalize(sample, agent, "files")
    second = normalize(sample, agent, "files")
    queue.ingest([first, first], destination="site")
    queue.ingest([second], destination="new-site")
    queue.resolve([], {str(first.message_id): "unknown_sensor"})
    assert queue.counts("site") == {"blocked": 1}
    assert queue.total_measurements() == 4
    queue.requeue_blocked("plant", str(agent), "site")
    assert queue.counts() == {"pending": 2}
    queue.resolve([str(first.message_id)], {})
    assert Queue(queue.path).total_measurements() == 4
