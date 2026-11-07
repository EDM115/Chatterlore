"""Add independent provider routes and typed provider operation settings.

Revision ID: 0018_provider_operation_profiles
Revises: 0017_scope_settings_versions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_provider_operation_profiles"
down_revision: str | None = "0017_scope_settings_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("chat_provider_profile", "summary_provider_profile", "embedding_provider_profile"):
        op.add_column("scope_settings", sa.Column(name, sa.String(64), nullable=True))
    op.add_column(
        "ai_provider_profiles",
        sa.Column("api_dialect", sa.String(16), nullable=False, server_default="auto"),
    )
    op.add_column(
        "ai_provider_profiles",
        sa.Column("auth_mode", sa.String(16), nullable=False, server_default="api_key"),
    )
    op.add_column(
        "ai_provider_profiles",
        sa.Column("local_chat_options", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "ai_provider_profiles",
        sa.Column("local_embedding_options", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    for name in ("local_embedding_options", "local_chat_options", "auth_mode", "api_dialect"):
        op.drop_column("ai_provider_profiles", name)
    for name in ("embedding_provider_profile", "summary_provider_profile", "chat_provider_profile"):
        op.drop_column("scope_settings", name)
