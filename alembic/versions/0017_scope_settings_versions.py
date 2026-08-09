"""Add optimistic versions to scope settings.

Revision ID: 0017_scope_settings_versions
Revises: 0016_managed_chat_enrollment_requests
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_scope_settings_versions"
down_revision: str | None = "0016_managed_chat_enrollment_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scope_settings", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )


def downgrade() -> None:
    op.drop_column("scope_settings", "version")
