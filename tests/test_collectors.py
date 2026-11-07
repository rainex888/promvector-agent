import asyncio
import os
import sqlite3
import time
from contextlib import aclosing

import pytest
from asyncua import Server
from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice
from test_communication import MemoryVault

from promvector_agent.collectors.databases import Database
from promvector_agent.collectors.files import Files
from promvector_agent.collectors.modbus import Modbus
from promvector_agent.collectors.opcua import OpcUA
from promvector_agent.config import FileSource, ModbusSource, OpcSource, SqliteSource
from promvector_agent.storage import Queue


async def test_csv_json_and_invalid_file(tmp_path):
    csv = tmp_path / "sample.csv"
    csv.write_text("speed,measured_at\n0042,2026-11-07T17:00:00+05:00\n")
    json = tmp_path / "sample.json"
    json.write_text('{"speed": 43, "measured_at": "2026-11-07T12:01:00Z"}')
    bad = tmp_path / "bad.csv"
    bad.write_text("speed,speed\n42,43\n")
    for path in (csv, json, bad):
        os.utime(path, (time.time() - 5, time.time() - 5))
    source = FileSource(
        id="import",
        equipment_id="pump",
        path=tmp_path,
        timestamp_column="measured_at",
        columns={"speed": {"name": "rpm", "unit": "rpm", "data_type": "integer"}},
    )
    queue = Queue(tmp_path / "queue.sqlite")
    collector = Files(source, queue, "test")
    collections = []
    with pytest.raises(ValueError, match="1 file"):
        async for collection in collector.collect():
            collections.append(collection)
    samples = [collection.samples[0] for collection in collections]
    assert [sample.metrics["rpm"].value for sample in samples] == [42, 43]
    assert samples[0].source_metadata["raw:speed"] == "0042"
    assert samples[0].timestamp.hour == 12
    queue.ingest([], file_key=collections[0].file_key)
    assert collector.load(csv) is None


async def test_sqlite_readonly_and_repeat_identity(tmp_path):
    path = tmp_path / "input.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE readings (id INTEGER, measured_at TEXT, speed INTEGER)")
        db.execute("INSERT INTO readings VALUES (1, '2026-11-07T12:00:00Z', 42)")
    source = SqliteSource(
        id="db",
        equipment_id="pump",
        path=path,
        query="SELECT id, measured_at, speed FROM readings",
        columns={"speed": {"name": "rpm"}},
        timestamp_column="measured_at",
        row_id_column="id",
    )
    collector = Database(source, MemoryVault(), "test")
    first = (await anext(collector.collect())).samples[0]
    second = (await anext(collector.collect())).samples[0]
    assert first.event_key == second.event_key and first.metrics["rpm"].value == 42
    for attack in ("DELETE FROM readings RETURNING *", "SELECT load_extension('example')"):
        broken = Database(source.model_copy(update={"query": attack}), MemoryVault(), "test")
        with pytest.raises(sqlite3.Error):
            await broken.test()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM readings").fetchone() == (1,)


async def test_modbus_real_tcp(unused_tcp_port):
    device = SimDevice(1, [SimData(0, values=1234, datatype=DataType.UINT16, readonly=True)])
    server = ModbusTcpServer(device, address=("127.0.0.1", unused_tcp_port))
    await server.serve_forever(background=True)
    try:
        source = ModbusSource(
            id="modbus",
            equipment_id="pump",
            host="127.0.0.1",
            port=unused_tcp_port,
            registers={"rpm": {"address": 0, "unit": "rpm"}},
        )
        collector = Modbus(source)
        await collector.test()
        result = (await anext(collector.collect())).samples[0]
        assert result.metrics["rpm"].value == 1234 and result.source_metadata["raw:rpm"] == "1234"
    finally:
        await server.shutdown()


async def test_opcua_poll_and_subscription_over_tcp(unused_tcp_port):
    server = Server()
    await server.init()
    endpoint = f"opc.tcp://127.0.0.1:{unused_tcp_port}/test/"
    server.set_endpoint(endpoint)
    namespace = await server.register_namespace("urn:promvector:test")
    node = await server.nodes.objects.add_variable(namespace, "RPM", 1400)
    async with server:
        source = OpcSource(
            id="opc",
            equipment_id="pump",
            endpoint=endpoint,
            interval=0.1,
            nodes={"rpm": {"node": node.nodeid.to_string(), "unit": "rpm"}},
            subscribe=False,
        )
        collector = OpcUA(source, MemoryVault(), "test")
        await collector.test()
        async with aclosing(collector.collect()) as observations:
            assert (await anext(observations)).samples[0].metrics["rpm"].value == 1400
        subscription = OpcUA(source.model_copy(update={"subscribe": True}), MemoryVault(), "test")
        async with aclosing(subscription.collect()) as observations, asyncio.timeout(10):
            assert (await anext(observations)).samples[0].metrics["rpm"].value == 1400
            await node.write_value(1410)
            result = (await anext(observations)).samples[0]
            assert result.metrics["rpm"].value == 1410
            assert result.source_metadata["timestamp_origin"] == "source"


@pytest.mark.skipif(
    not os.environ.get("PV_EDGE_POSTGRES_SOURCE_DSN"), reason="Requires a disposable TLS PostgreSQL"
)
async def test_postgresql_readonly_tls():
    import psycopg

    from promvector_agent.collectors.databases import source_secret
    from promvector_agent.config import PostgresSource

    dsn = os.environ["PV_EDGE_POSTGRES_SOURCE_DSN"]
    source = PostgresSource(
        id="pg",
        equipment_id="pump",
        query="SELECT 1 AS id, CURRENT_TIMESTAMP AS measured_at, 42 AS speed WHERE 'sensor01' LIKE 'sensor%'",
        columns={"speed": {"name": "rpm"}},
        timestamp_column="measured_at",
        row_id_column="id",
    )
    vault = MemoryVault()
    vault.set(source_secret(source, "test"), dsn)
    collector = Database(source, vault, "test")
    await collector.test()
    result = (await anext(collector.collect())).samples[0]
    assert result.metrics["rpm"].value == 42

    write = source.model_copy(
        update={
            "query": "WITH altered AS (DELETE FROM factories RETURNING id) "
            "SELECT id, CURRENT_TIMESTAMP AS measured_at, 42 AS speed FROM altered"
        }
    )
    with pytest.raises(psycopg.Error):
        await Database(write, vault, "test").test()
