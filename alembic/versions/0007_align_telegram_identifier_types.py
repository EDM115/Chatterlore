"""align Telegram identifier column types

Revision ID: 0007_align_telegram_identifier_types
Revises: 0006_phase6_product_completeness
Create Date: 2026-07-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_align_telegram_identifier_types"
down_revision: str | None = "0006_phase6_product_completeness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TELEGRAM_IDENTIFIER_COLUMNS = {
    "bot_events": (("chat_id", True), ("user_id", True)),
    "embeddings": (("chat_id", True),),
    "knowledge_items": (("chat_id", True),),
    "learning_policies": (("chat_id", True), ("user_id", True), ("updated_by", True)),
    "messages": (("chat_id", False), ("user_id", True)),
}


def _alter_identifier_types(
    source_type: sa.types.TypeEngine, target_type: sa.types.TypeEngine
) -> None:
    for table_name, columns in TELEGRAM_IDENTIFIER_COLUMNS.items():
        with op.batch_alter_table(table_name) as batch_op:
            for column_name, nullable in columns:
                batch_op.alter_column(
                    column_name,
                    existing_type=source_type,
                    type_=target_type,
                    existing_nullable=nullable,
                )


def upgrade() -> None:
    _alter_identifier_types(sa.Integer(), sa.BigInteger())


def downgrade() -> None:
    _alter_identifier_types(sa.BigInteger(), sa.Integer())
