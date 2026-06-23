"""phase 5 operations and settings

Revision ID: 0005_phase5_operations_settings
Revises: 0004_phase3_provider_ai_hardening
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_phase5_operations_settings"
down_revision: str | None = "0004_phase3_provider_ai_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("key", name="uq_runtime_settings_key"),
    )
    op.create_table(
        "forget_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_forget_challenges_scope_user",
        "forget_challenges",
        ["chat_id", "message_thread_id", "user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_forget_challenges_scope_user", table_name="forget_challenges")
    op.drop_table("forget_challenges")
    op.drop_table("runtime_settings")
