"""add the bot-admin alert outbox

Revision ID: 0015_admin_alert_outbox
Revises: 0014_provider_credential_pools
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_admin_alert_outbox"
down_revision: str | None = "0014_provider_credential_pools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_intent_id", sa.Integer(), nullable=True),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("profile_name", sa.String(length=64), nullable=False),
        sa.Column("credential_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("credential_label", sa.String(length=64), nullable=True),
        sa.Column("health_epoch", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("recipient_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_classification", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["parent_intent_id"], ["admin_alerts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_admin_alerts_dedupe_key", "admin_alerts", ["dedupe_key"], unique=True)
    op.create_index(
        "ix_admin_alerts_delivery_due",
        "admin_alerts",
        ["state", "next_attempt_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_admin_alerts_profile_created",
        "admin_alerts",
        ["profile_name", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_alerts_profile_created", table_name="admin_alerts")
    op.drop_index("ix_admin_alerts_delivery_due", table_name="admin_alerts")
    op.drop_index("uq_admin_alerts_dedupe_key", table_name="admin_alerts")
    op.drop_table("admin_alerts")
