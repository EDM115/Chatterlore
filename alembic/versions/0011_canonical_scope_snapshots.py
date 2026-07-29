"""add canonical scope snapshots

Revision ID: 0011_canonical_scope_snapshots
Revises: 0010_remove_cross_scope_can_learn
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_canonical_scope_snapshots"
down_revision: str | None = "0010_remove_cross_scope_can_learn"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canonical_scope_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("item_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("entries_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_profile", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("reasoning_level", sa.String(length=32), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_key", name="uq_canonical_scope_snapshots_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("canonical_scope_snapshots")
