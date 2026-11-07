"""Add standalone agent enrollment and idempotent receipt storage to PromVector PostgreSQL."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "edge_agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("factory_id", sa.String(24), nullable=False),
        sa.Column("equipment_ids", sa.ARRAY(sa.String(24)), nullable=False),
        sa.Column("enrollment_hash", sa.String(64), nullable=False),
        sa.Column("enrollment_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("allow_simulated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("version", sa.String(32)),
        sa.Column("pending", sa.Integer()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("enrollment_hash"),
        sa.UniqueConstraint("token_hash"),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"]),
    )
    op.create_table(
        "edge_telemetry_receipts",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("message_id"),
        sa.ForeignKeyConstraint(["agent_id"], ["edge_agents.id"]),
    )
    op.create_index("ix_edge_telemetry_receipts_agent_id", "edge_telemetry_receipts", ["agent_id"])


def downgrade():
    op.drop_table("edge_telemetry_receipts")
    op.drop_table("edge_agents")
