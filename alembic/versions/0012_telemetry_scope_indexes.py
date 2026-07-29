"""normalize telemetry scope and add query indexes

Revision ID: 0012_telemetry_scope_indexes
Revises: 0011_canonical_scope_snapshots
Create Date: 2026-07-29
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0012_telemetry_scope_indexes"
down_revision: str | None = "0011_canonical_scope_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_usage", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("ai_usage", sa.Column("message_thread_id", sa.Integer(), nullable=True))
    _backfill_usage_scopes()
    op.create_index(
        "ix_ai_usage_scope_created_at",
        "ai_usage",
        ["chat_id", "message_thread_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_usage_operation_created_at", "ai_usage", ["operation", "created_at"], unique=False
    )
    op.create_index(
        "ix_ai_usage_error_created_at", "ai_usage", ["error", "created_at"], unique=False
    )
    op.create_index(
        "ix_bot_events_type_scope_created_at",
        "bot_events",
        ["event_type", "chat_id", "message_thread_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_bot_events_scope_created_at",
        "bot_events",
        ["chat_id", "message_thread_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_bot_events_scope_created_at", table_name="bot_events")
    op.drop_index("ix_bot_events_type_scope_created_at", table_name="bot_events")
    op.drop_index("ix_ai_usage_error_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_operation_created_at", table_name="ai_usage")
    op.drop_index("ix_ai_usage_scope_created_at", table_name="ai_usage")
    op.drop_column("ai_usage", "message_thread_id")
    op.drop_column("ai_usage", "chat_id")


def _backfill_usage_scopes() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, metadata_json FROM ai_usage")).mappings()
    for row in rows:
        scope = _scope_columns(row["metadata_json"])
        if scope is None:
            continue
        connection.execute(
            sa.text(
                "UPDATE ai_usage "
                "SET chat_id = :chat_id, message_thread_id = :message_thread_id "
                "WHERE id = :id"
            ),
            {"id": int(row["id"]), "chat_id": scope[0], "message_thread_id": scope[1]},
        )


def _scope_columns(metadata_json: Any) -> tuple[int | None, int | None] | None:
    payload = metadata_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    raw_scope = payload.get("scope")
    if not isinstance(raw_scope, str):
        return None
    clean = raw_scope.strip().lower()
    if clean in {"all", "all:main"}:
        return (None, None)
    parts = clean.split(":")
    if len(parts) not in {1, 2}:
        return None
    try:
        chat_id = int(parts[0])
        thread_id = None if len(parts) == 1 or parts[1] in {"", "main", "none"} else int(parts[1])
    except ValueError:
        return None
    return (chat_id, thread_id)
