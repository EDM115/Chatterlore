"""phase 6 product completeness

Revision ID: 0006_phase6_product_completeness
Revises: 0005_phase5_operations_settings
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_phase6_product_completeness"
down_revision: str | None = "0005_phase5_operations_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("updates", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_settings_challenges_scope_user",
        "settings_challenges",
        ["chat_id", "message_thread_id", "user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_settings_challenges_scope_user", table_name="settings_challenges")
    op.drop_table("settings_challenges")
