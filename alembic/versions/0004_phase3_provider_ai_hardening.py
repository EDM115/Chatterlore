"""phase 3 provider and ai hardening

Revision ID: 0004_phase3_provider_ai_hardening
Revises: 0003_phase2_remaining_memory_tools
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_phase3_provider_ai_hardening"
down_revision: str | None = "0003_phase2_remaining_memory_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scope_settings", sa.Column("system_prompt_template", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "scope_settings", sa.Column("provider_profile", sa.String(length=64), nullable=True)
    )
    op.add_column("scope_settings", sa.Column("chat_model", sa.String(length=255), nullable=True))
    op.add_column(
        "scope_settings", sa.Column("embedding_model", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "scope_settings", sa.Column("reasoning_level", sa.String(length=32), nullable=True)
    )
    op.add_column("ai_usage", sa.Column("metadata_json", sa.JSON(), nullable=True))
    op.create_table(
        "system_prompt_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_system_prompt_templates_name"),
    )


def downgrade() -> None:
    op.drop_table("system_prompt_templates")
    op.drop_column("ai_usage", "metadata_json")
    op.drop_column("scope_settings", "reasoning_level")
    op.drop_column("scope_settings", "embedding_model")
    op.drop_column("scope_settings", "chat_model")
    op.drop_column("scope_settings", "provider_profile")
    op.drop_column("scope_settings", "system_prompt_template")
