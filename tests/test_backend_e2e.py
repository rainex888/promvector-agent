import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
import trustme
from test_communication import MemoryVault

from promvector_agent.config import Backend, Settings, SimulatorSource
from promvector_agent.runtime import Runtime
from promvector_agent.storage import Queue


@pytest.mark.skipif(
    not os.environ.get("PV_EDGE_TEST_DSN"), reason="Requires a disposable patched backend PostgreSQL"
)
async def test_simulator_https_fastapi_postgresql(tmp_path, unused_tcp_port):
    dsn = os.environ["PV_EDGE_TEST_DSN"]
    stage = Path(os.environ["PV_EDGE_BACKEND_STAGE"])
    env = {
        **os.environ,
        "DATABASE_URL": dsn,
        "PYTHONPATH": os.environ["PV_EDGE_BACKEND_PYTHONPATH"],
        "MQTT_HOST": "",
        "PUBLIC_DOCS": "false",
    }
    factory = f"test-{uuid4().hex[:10]}"
    equipment = factory + "-pump"
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "INSERT INTO factories (id, name, lines) VALUES (%s, %s, ARRAY['lab'])",
            (factory, "Agent integration test"),
        )
        connection.execute(
            "INSERT INTO equipment (id, factory_id, name, type, line, model) "
            "VALUES (%s, %s, '[SIMULATOR] Pump', 'Pump', 'lab', '')",
            (equipment, factory),
        )
        for code, channel, unit in (
            ("temperature", "temperature", "C"),
            ("pressure", "pressure", "bar"),
            ("rpm", "utilization", "rpm"),
            ("vibration", "vibration", "mm/s"),
        ):
            connection.execute(
                "INSERT INTO sensors (id, equipment_id, channel, code, position, unit) VALUES (%s,%s,%s,%s,'',%s)",
                (uuid4(), equipment, channel, code, unit),
            )

    ca_path, cert_path, key_path = [tmp_path / path for path in ("ca.pem", "cert.pem", "key.pem")]
    ca = trustme.CA()
    ca.cert_pem.write_to_path(ca_path)
    cert = ca.issue_cert("127.0.0.1")
    cert.cert_chain_pems[0].write_to_path(cert_path)
    cert.private_key_pem.write_to_path(key_path)
    origin = f"https://127.0.0.1:{unused_tcp_port}"
    source = SimulatorSource(id="demo", equipment_id=equipment, authorized=True, enabled=True, interval=0.1)
    settings = Settings(
        enterprise_id=factory,
        sources=[source],
        backend=Backend(url=origin, ca_file=ca_path, send_simulated=True, interval=0.2, heartbeat_interval=1),
    )
    admin = [sys.executable, "-m", "promvector.agent_admin"]
    enrollment = subprocess.check_output(
        [*admin, "provision", str(settings.agent_id), factory, equipment, "--allow-simulated"],
        text=True,
        cwd=stage,
        env=env,
    ).strip()
    queue = Queue(tmp_path / "queue.sqlite")
    runtime = Runtime(settings, queue, MemoryVault())
    agent = runtime.communication
    collector = runtime.collector(source)
    runtime.persist(source, await anext(collector.collect()))
    with pytest.raises(httpx.ConnectError):
        async with agent.connect() as backend:
            await agent.send_once(backend, "x" * 64)
    assert queue.counts() == {"pending": 1}

    with (tmp_path / "backend.log").open("w+") as log:
        service = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "promvector.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(unused_tcp_port),
                "--ssl-certfile",
                str(cert_path),
                "--ssl-keyfile",
                str(key_path),
            ],
            cwd=stage,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(
                verify=__import__("ssl").create_default_context(cafile=str(ca_path))
            ) as http:
                async with asyncio.timeout(15):
                    while True:
                        try:
                            if (await http.get(f"{origin}/ready")).status_code == 200:
                                break
                        except httpx.ConnectError:
                            if service.poll() is not None:
                                log.seek(0)
                                pytest.fail(log.read())
                        await asyncio.sleep(0.1)

            await agent.register(enrollment)
            await agent.register(enrollment)
            remote = await agent.check()
            assert remote.equipment_ids == [equipment] and remote.allow_simulated
            pending = queue.pending(factory, str(settings.agent_id), 1, True, agent.scope)
            assert pending[0].simulated
            async with agent.connect() as backend:
                receipts = await backend.send(agent.token(), pending)
                assert receipts[0].status == "accepted"
                assert queue.counts() == {"pending": 1}
                await agent.send_once(backend, agent.token())
            assert queue.counts() == {"delivered": 1}
            with psycopg.connect(dsn) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM telemetry JOIN sensors ON sensors.id=telemetry.sensor_id "
                    "WHERE equipment_id=%s",
                    (equipment,),
                ).fetchone() == (4,)
                stored = connection.execute(
                    "SELECT payload FROM edge_telemetry_receipts WHERE message_id=%s",
                    (pending[0].message_id,),
                ).fetchone()[0]
                assert stored["simulated"] and "operating_state" in stored["metrics"]
                assert connection.execute(
                    "SELECT count(*) FROM ai_analysis_jobs WHERE equipment_id=%s", (equipment,)
                ).fetchone() == (1,)

            subprocess.run([*admin, "revoke", str(settings.agent_id)], cwd=stage, env=env, check=True)
            with pytest.raises(httpx.HTTPStatusError) as denied:
                await agent.check()
            assert denied.value.response.status_code == 401
        finally:
            service.terminate()
            try:
                service.wait(timeout=8)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait()
