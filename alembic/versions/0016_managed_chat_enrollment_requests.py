"""add managed chat enrollment requests

Revision ID: 0016_managed_chat_enrollment_requests
Revises: 0015_admin_alert_outbox
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_managed_chat_enrollment_requests"
down_revision: str | None = "0015_admin_alert_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE managed_chats SET status = 'detached' WHERE status = 'removed'")
    op.create_table(
        "managed_chat_enrollment_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False),
        sa.Column("prepared_button_id", sa.String(length=255), nullable=True),
        sa.Column("delivery", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_managed_chat_enrollment_requests_request_id",
        "managed_chat_enrollment_requests",
        ["request_id"],
        unique=True,
    )
    op.create_index(
        "ix_managed_chat_enrollment_requests_requester_status",
        "managed_chat_enrollment_requests",
        ["requested_by", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_managed_chat_enrollment_requests_requester_status",
        table_name="managed_chat_enrollment_requests",
    )
    op.drop_index(
        "uq_managed_chat_enrollment_requests_request_id",
        table_name="managed_chat_enrollment_requests",
    )
    op.drop_table("managed_chat_enrollment_requests")
    op.execute("UPDATE managed_chats SET status = 'removed' WHERE status = 'detached'")
