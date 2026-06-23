"""phase 2 remaining memory tools

Revision ID: 0003_phase2_remaining_memory_tools
Revises: 0002_phase2_memory_quality
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_phase2_remaining_memory_tools"
down_revision: str | None = "0002_phase2_memory_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("metadata_json", sa.JSON(), nullable=True))
    op.create_table(
        "memory_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_memory_profiles_name"),
    )
    op.create_table(
        "memory_profile_scopes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("memory_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_thread_id", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "profile_id", "chat_id", "message_thread_id", name="uq_memory_profile_scope"
        ),
    )
    op.create_index("ix_memory_profile_scopes_profile", "memory_profile_scopes", ["profile_id"])
    op.create_index(
        "ix_memory_profile_scopes_scope", "memory_profile_scopes", ["chat_id", "message_thread_id"]
    )
    op.create_table(
        "memory_scope_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("destination_thread_id", sa.Integer(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_thread_id", sa.Integer(), nullable=True),
        sa.Column("can_query", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("can_learn", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "destination_chat_id",
            "destination_thread_id",
            "source_chat_id",
            "source_thread_id",
            name="uq_memory_scope_permission",
        ),
    )
    op.create_index(
        "ix_memory_scope_permissions_destination",
        "memory_scope_permissions",
        ["destination_chat_id", "destination_thread_id"],
    )
    op.create_index(
        "ix_memory_scope_permissions_source",
        "memory_scope_permissions",
        ["source_chat_id", "source_thread_id"],
    )
    op.create_table(
        "summary_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_summary_templates_name"),
    )


def downgrade() -> None:
    op.drop_table("summary_templates")
    op.drop_index("ix_memory_scope_permissions_source", table_name="memory_scope_permissions")
    op.drop_index("ix_memory_scope_permissions_destination", table_name="memory_scope_permissions")
    op.drop_table("memory_scope_permissions")
    op.drop_index("ix_memory_profile_scopes_scope", table_name="memory_profile_scopes")
    op.drop_index("ix_memory_profile_scopes_profile", table_name="memory_profile_scopes")
    op.drop_table("memory_profile_scopes")
    op.drop_table("memory_profiles")
    op.drop_column("knowledge_items", "metadata_json")
