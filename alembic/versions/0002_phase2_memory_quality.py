"""phase 2 memory quality

Revision ID: 0002_phase2_memory_quality
Revises: 0001_initial
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase2_memory_quality"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("chat_title", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("chat_username", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("topic_title", sa.String(length=255), nullable=True))
    op.add_column("messages", sa.Column("message_permalink", sa.String(length=512), nullable=True))
    op.create_table(
        "scope_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("chat_title", sa.String(length=255), nullable=True),
        sa.Column("topic_title", sa.String(length=255), nullable=True),
        sa.Column("chat_username", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "message_thread_id", name="uq_scope_metadata"),
    )
    op.create_index("ix_scope_metadata_scope", "scope_metadata", ["chat_id", "message_thread_id"])
    op.create_table(
        "scope_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("retrieval_top_k", sa.Integer(), nullable=True),
        sa.Column("summary_batch_size", sa.Integer(), nullable=True),
        sa.Column("summary_model", sa.String(length=255), nullable=True),
        sa.Column("raw_message_retention_days", sa.Integer(), nullable=True),
        sa.Column("knowledge_retention_days", sa.Integer(), nullable=True),
        sa.Column("review_before_compact", sa.Boolean(), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "message_thread_id", name="uq_scope_settings"),
    )
    op.create_index("ix_scope_settings_scope", "scope_settings", ["chat_id", "message_thread_id"])
    op.create_table(
        "memory_review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_message_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_memory_review_scope_status",
        "memory_review_items",
        ["chat_id", "message_thread_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_review_scope_status", table_name="memory_review_items")
    op.drop_table("memory_review_items")
    op.drop_index("ix_scope_settings_scope", table_name="scope_settings")
    op.drop_table("scope_settings")
    op.drop_index("ix_scope_metadata_scope", table_name="scope_metadata")
    op.drop_table("scope_metadata")
    op.drop_column("messages", "message_permalink")
    op.drop_column("messages", "topic_title")
    op.drop_column("messages", "chat_username")
    op.drop_column("messages", "chat_title")
