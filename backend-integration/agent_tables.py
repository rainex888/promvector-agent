from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from .models import Base

agents = Table(
    "edge_agents",
    Base.metadata,
    Column("id", Uuid, primary_key=True),
    Column("factory_id", String(24), ForeignKey("factories.id"), nullable=False),
    Column("equipment_ids", ARRAY(String(24)), nullable=False),
    Column("enrollment_hash", String(64), nullable=False, unique=True),
    Column("enrollment_until", DateTime(timezone=True), nullable=False),
    Column("token_hash", String(64), unique=True),
    Column("allow_simulated", Boolean, nullable=False, server_default=false()),
    Column("revoked_at", DateTime(timezone=True)),
    Column("last_heartbeat", DateTime(timezone=True)),
    Column("version", String(32)),
    Column("pending", Integer),
)

receipts = Table(
    "edge_telemetry_receipts",
    Base.metadata,
    Column("message_id", Uuid, primary_key=True),
    Column("agent_id", Uuid, ForeignKey("edge_agents.id"), nullable=False, index=True),
    Column("payload", JSONB, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
