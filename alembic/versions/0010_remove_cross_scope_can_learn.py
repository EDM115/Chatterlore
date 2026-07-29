"""remove unused cross-scope learning permission

Revision ID: 0010_remove_cross_scope_can_learn
Revises: 0009_summary_claims
Create Date: 2026-07-29

The removed can_learn values are intentionally discarded because no runtime path ever consumed
them. Cross-scope access remains retrieval-only through can_query plus the source scope's own
learning policy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_remove_cross_scope_can_learn"
down_revision: str | None = "0009_summary_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_scope_permissions") as batch_op:
        batch_op.drop_column("can_learn")


def downgrade() -> None:
    with op.batch_alter_table("memory_scope_permissions") as batch_op:
        batch_op.add_column(
            sa.Column("can_learn", sa.Boolean(), nullable=False, server_default=sa.false())
        )
