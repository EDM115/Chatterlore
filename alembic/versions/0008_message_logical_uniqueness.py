"""enforce logical message uniqueness

Revision ID: 0008_message_logical_uniqueness
Revises: 0007_align_telegram_identifier_types
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_message_logical_uniqueness"
down_revision: str | None = "0007_align_telegram_identifier_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAIN_THREAD_SENTINEL = -1
INDEX_NAME = "uq_messages_logical_scope"

messages = sa.table(
    "messages",
    sa.column("id", sa.Integer()),
    sa.column("chat_id", sa.BigInteger()),
    sa.column("message_thread_id", sa.Integer()),
    sa.column("telegram_message_id", sa.Integer()),
)
embeddings = sa.table(
    "embeddings",
    sa.column("id", sa.Integer()),
    sa.column("target_type", sa.String()),
    sa.column("target_id", sa.Integer()),
    sa.column("provider", sa.String()),
    sa.column("model", sa.String()),
)


def _reconcile_duplicate_messages() -> None:
    connection = op.get_bind()
    groups = connection.execute(
        sa.select(
            messages.c.chat_id,
            sa.func.coalesce(messages.c.message_thread_id, MAIN_THREAD_SENTINEL).label(
                "logical_thread_id"
            ),
            messages.c.telegram_message_id,
        )
        .group_by(
            messages.c.chat_id,
            sa.func.coalesce(messages.c.message_thread_id, MAIN_THREAD_SENTINEL),
            messages.c.telegram_message_id,
        )
        .having(sa.func.count(messages.c.id) > 1)
    ).mappings()
    for group in groups:
        message_ids = list(
            connection.scalars(
                sa.select(messages.c.id)
                .where(
                    messages.c.chat_id == group["chat_id"],
                    sa.func.coalesce(messages.c.message_thread_id, MAIN_THREAD_SENTINEL)
                    == group["logical_thread_id"],
                    messages.c.telegram_message_id == group["telegram_message_id"],
                )
                .order_by(messages.c.id.asc())
            )
        )
        keeper_id, *duplicate_ids = message_ids
        seen_embeddings: set[tuple[str, str]] = set()
        embedding_rows = connection.execute(
            sa.select(
                embeddings.c.id, embeddings.c.target_id, embeddings.c.provider, embeddings.c.model
            )
            .where(embeddings.c.target_type == "message", embeddings.c.target_id.in_(message_ids))
            .order_by(embeddings.c.id.asc())
        ).mappings()
        for embedding in embedding_rows:
            key = (str(embedding["provider"]), str(embedding["model"]))
            if key in seen_embeddings:
                connection.execute(sa.delete(embeddings).where(embeddings.c.id == embedding["id"]))
                continue
            seen_embeddings.add(key)
            if embedding["target_id"] != keeper_id:
                connection.execute(
                    sa.update(embeddings)
                    .where(embeddings.c.id == embedding["id"])
                    .values(target_id=keeper_id)
                )
        connection.execute(sa.delete(messages).where(messages.c.id.in_(duplicate_ids)))


def upgrade() -> None:
    _reconcile_duplicate_messages()
    op.create_index(
        INDEX_NAME,
        "messages",
        [
            "chat_id",
            sa.text(f"COALESCE(message_thread_id, {MAIN_THREAD_SENTINEL})"),
            "telegram_message_id",
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="messages")
