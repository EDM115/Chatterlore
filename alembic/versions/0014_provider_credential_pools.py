"""add provider profiles, credential pools, and bound step-up state

Revision ID: 0014_provider_credential_pools
Revises: 0013_control_plane_identity_access
Create Date: 2026-08-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_provider_credential_pools"
down_revision: str | None = "0013_control_plane_identity_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("control_plane_sessions") as batch_op:
        batch_op.add_column(sa.Column("step_up_action", sa.String(length=64), nullable=True))

    op.create_table(
        "ai_provider_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("provider_kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("chat_model", sa.String(length=255), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column("chat_capable", sa.Boolean(), nullable=False),
        sa.Column("embedding_capable", sa.Boolean(), nullable=False),
        sa.Column("reasoning_capable", sa.Boolean(), nullable=False),
        sa.Column("local", sa.Boolean(), nullable=False),
        sa.Column("redact_before_ai", sa.Boolean(), nullable=False),
        sa.Column("redaction_patterns", sa.JSON(), nullable=False),
        sa.Column("fake_embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_ai_provider_profiles_name", "ai_provider_profiles", ["name"], unique=True)
    op.create_index(
        "ix_ai_provider_profiles_enabled", "ai_provider_profiles", ["enabled", "name"], unique=False
    )

    op.create_table(
        "ai_provider_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("environment_variable", sa.String(length=255), nullable=True),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=True),
        sa.Column("wrap_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("master_key_id", sa.String(length=64), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("last_four", sa.String(length=16), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=32), nullable=False),
        sa.Column("health_state", sa.String(length=32), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_classification", sa.String(length=64), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_by", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["ai_provider_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_provider_credentials_profile_position",
        "ai_provider_credentials",
        ["profile_id", "position", "id"],
        unique=False,
    )
    op.create_index(
        "ix_ai_provider_credentials_profile_state",
        "ai_provider_credentials",
        ["profile_id", "lifecycle_state", "cooldown_until"],
        unique=False,
    )
    op.create_index(
        "ix_ai_provider_credentials_fingerprint",
        "ai_provider_credentials",
        ["profile_id", "fingerprint"],
        unique=False,
    )

    op.create_table(
        "ai_provider_pool_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("active_credential_id", sa.Integer(), nullable=True),
        sa.Column("health_epoch", sa.Integer(), nullable=False),
        sa.Column("last_exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_credential_id"], ["ai_provider_credentials.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["ai_provider_profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_ai_provider_pool_state_profile_id",
        "ai_provider_pool_state",
        ["profile_id"],
        unique=True,
    )
    op.create_index(
        "ix_ai_provider_pool_state_active",
        "ai_provider_pool_state",
        ["active_credential_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_provider_pool_state_active", table_name="ai_provider_pool_state")
    op.drop_index("uq_ai_provider_pool_state_profile_id", table_name="ai_provider_pool_state")
    op.drop_table("ai_provider_pool_state")
    op.drop_index("ix_ai_provider_credentials_fingerprint", table_name="ai_provider_credentials")
    op.drop_index("ix_ai_provider_credentials_profile_state", table_name="ai_provider_credentials")
    op.drop_index(
        "ix_ai_provider_credentials_profile_position", table_name="ai_provider_credentials"
    )
    op.drop_table("ai_provider_credentials")
    op.drop_index("ix_ai_provider_profiles_enabled", table_name="ai_provider_profiles")
    op.drop_index("uq_ai_provider_profiles_name", table_name="ai_provider_profiles")
    op.drop_table("ai_provider_profiles")
    with op.batch_alter_table("control_plane_sessions") as batch_op:
        batch_op.drop_column("step_up_action")
