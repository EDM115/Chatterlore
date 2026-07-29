"""add persisted summary claims

Revision ID: 0009_summary_claims
Revises: 0008_message_logical_uniqueness
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_summary_claims"
down_revision: str | None = "0008_message_logical_uniqueness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_messages_summary_claim"
LOGICAL_MESSAGE_INDEX = "uq_messages_logical_scope"
MAIN_THREAD_SENTINEL = -1


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("summary_claim_token", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("summary_claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index(
            INDEX_NAME,
            ["learned", "summarized_at", "chat_id", "message_thread_id", "summary_claimed_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(LOGICAL_MESSAGE_INDEX, table_name="messages")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_index(INDEX_NAME)
        batch_op.drop_column("summary_claimed_at")
        batch_op.drop_column("summary_claim_token")
    op.create_index(
        LOGICAL_MESSAGE_INDEX,
        "messages",
        [
            "chat_id",
            sa.text(f"COALESCE(message_thread_id, {MAIN_THREAD_SENTINEL})"),
            "telegram_message_id",
        ],
        unique=True,
    )
