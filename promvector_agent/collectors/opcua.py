import asyncio
from contextlib import asynccontextmanager

from ..config import OpcSource
from ..models import Metric, Sample
from ..security import Vault
from . import Collection
from .databases import source_secret


class SubscriptionHandler:
    def __init__(self):
        self.events = asyncio.Queue(maxsize=4096)
        self.failure = None

    def datachange_notification(self, node, value, data):
        try:
            self.events.put_nowait((node.nodeid, data.monitored_item.Value))
        except asyncio.QueueFull:
            self.failure = "OPC UA notification buffer overflowed"

    def status_change_notification(self, status):
        if not status.Status.is_good():
            self.failure = "OPC UA server reported a subscription failure"


class OpcUA:
    def __init__(self, source: OpcSource, vault: Vault, namespace: str):
        self.source = source
        self.vault = vault
        self.namespace = namespace

    @asynccontextmanager
    async def connection(self):
        from asyncua import Client
        from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256

        source = self.source
        client = Client(source.endpoint, timeout=5)
        if source.client_cert:
            await client.set_security(
                SecurityPolicyBasic256Sha256,
                source.client_cert,
                source.client_key,
                server_certificate=source.server_cert,
            )
        if source.username:
            password = self.vault.get(source_secret(source, self.namespace))
            if not password:
                raise ValueError("Store the OPC UA password in the OS credential store first")
            client.set_user(source.username)
            client.set_password(password)
        async with client:
            nodes = {name: client.get_node(item.node) for name, item in source.nodes.items()}
            yield client, nodes

    def sample(self, name, reading):
        if reading.Value is None or reading.Value.Value is None:
            raise ValueError("OPC UA node has no scalar value")
        value = reading.Value.Value
        if type(value) not in (int, float, bool, str):
            raise ValueError("OPC UA node value is not scalar")
        timestamp = reading.SourceTimestamp or reading.ServerTimestamp
        quality = (
            "good"
            if reading.StatusCode.is_good()
            else ("bad" if reading.StatusCode.is_bad() else "uncertain")
        )
        node = self.source.nodes[name]
        metadata = {
            "node": node.node,
            "timestamp_origin": "source" if reading.SourceTimestamp else ("server" if timestamp else "agent"),
            "opc_status": reading.StatusCode.name,
        }
        kwargs = {"timestamp": timestamp} if timestamp else {}
        event_key = None
        if timestamp:
            event_key = repr((node.node, timestamp.isoformat(), value, quality))
        return Sample(
            **kwargs,
            metrics={name: Metric(value=value, unit=node.unit)},
            quality=quality,
            source_metadata=metadata,
            event_key=event_key,
        )

    async def poll(self, nodes):
        for name, node in nodes.items():
            reading = await node.read_data_value(raise_on_bad_status=False)
            yield Collection([self.sample(name, reading)])

    async def test(self):
        async with self.connection() as (_, nodes):
            async for result in self.poll(nodes):
                if result.samples[0].quality == "bad":
                    raise ValueError("An OPC UA node returned bad quality")

    async def collect(self):
        async with self.connection() as (client, nodes):
            if not self.source.subscribe:
                async for result in self.poll(nodes):
                    yield result
                return

            handler = SubscriptionHandler()
            subscription = await client.create_subscription(self.source.interval * 1000, handler)
            by_id = {node.nodeid: name for name, node in nodes.items()}
            try:
                handles = await subscription.subscribe_data_change(list(nodes.values()), queuesize=64)
                if any(not isinstance(handle, int) for handle in handles):
                    raise ValueError("One or more OPC UA nodes rejected subscription")
                while True:
                    if handler.failure:
                        raise ConnectionError(handler.failure)
                    await client.check_connection()
                    try:
                        async with asyncio.timeout(2):
                            node_id, value = await handler.events.get()
                    except TimeoutError:
                        continue
                    yield Collection([self.sample(by_id[node_id], value)])
            finally:
                await subscription.delete()
