"""add control-plane identity and access schema

Revision ID: 0013_control_plane_identity_access
Revises: 0012_telemetry_scope_indexes
Create Date: 2026-07-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_control_plane_identity_access"
down_revision: str | None = "0012_telemetry_scope_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "control_plane_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("language_code", sa.String(length=32), nullable=True),
        sa.Column("photo_url", sa.String(length=512), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column("is_premium", sa.Boolean(), nullable=True),
        sa.Column("first_authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_control_plane_users_telegram_user_id",
        "control_plane_users",
        ["telegram_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_control_plane_users_last_authenticated",
        "control_plane_users",
        ["last_authenticated_at"],
        unique=False,
    )

    op.create_table(
        "control_plane_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("client_kind", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["control_plane_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_control_plane_sessions_token_hash",
        "control_plane_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_control_plane_sessions_user_active",
        "control_plane_sessions",
        ["user_id", "revoked_at", "absolute_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_control_plane_sessions_expiry",
        "control_plane_sessions",
        ["idle_expires_at", "absolute_expires_at"],
        unique=False,
    )

    op.create_table(
        "oidc_auth_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("pkce_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("pkce_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("oidc_nonce", sa.String(length=128), nullable=False),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("return_to", sa.String(length=512), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("expected_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_oidc_auth_transactions_state_hash",
        "oidc_auth_transactions",
        ["state_hash"],
        unique=True,
    )
    op.create_index(
        "ix_oidc_auth_transactions_expiry",
        "oidc_auth_transactions",
        ["expires_at", "consumed_at"],
        unique=False,
    )

    op.create_table(
        "bot_admin_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_bot_admin_grants_telegram_user_id",
        "bot_admin_grants",
        ["telegram_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_bot_admin_grants_active",
        "bot_admin_grants",
        ["revoked_at", "telegram_user_id"],
        unique=False,
    )

    op.create_table(
        "chat_admin_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_chat_admin_grants_scope_user",
        "chat_admin_grants",
        ["chat_id", "telegram_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_chat_admin_grants_user_active",
        "chat_admin_grants",
        ["telegram_user_id", "revoked_at", "chat_id"],
        unique=False,
    )

    op.create_table(
        "managed_chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enrollment_token_hash", sa.String(length=64), nullable=True),
        sa.Column("enrollment_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_managed_chats_chat_id", "managed_chats", ["chat_id"], unique=True)
    op.create_index(
        "ix_managed_chats_status", "managed_chats", ["status", "updated_at"], unique=False
    )
    op.create_index(
        "ix_managed_chats_enrollment_expiry",
        "managed_chats",
        ["enrollment_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_managed_chats_enrollment_expiry", table_name="managed_chats")
    op.drop_index("ix_managed_chats_status", table_name="managed_chats")
    op.drop_index("uq_managed_chats_chat_id", table_name="managed_chats")
    op.drop_table("managed_chats")
    op.drop_index("ix_chat_admin_grants_user_active", table_name="chat_admin_grants")
    op.drop_index("uq_chat_admin_grants_scope_user", table_name="chat_admin_grants")
    op.drop_table("chat_admin_grants")
    op.drop_index("ix_bot_admin_grants_active", table_name="bot_admin_grants")
    op.drop_index("uq_bot_admin_grants_telegram_user_id", table_name="bot_admin_grants")
    op.drop_table("bot_admin_grants")
    op.drop_index("ix_oidc_auth_transactions_expiry", table_name="oidc_auth_transactions")
    op.drop_index("uq_oidc_auth_transactions_state_hash", table_name="oidc_auth_transactions")
    op.drop_table("oidc_auth_transactions")
    op.drop_index("ix_control_plane_sessions_expiry", table_name="control_plane_sessions")
    op.drop_index("ix_control_plane_sessions_user_active", table_name="control_plane_sessions")
    op.drop_index("uq_control_plane_sessions_token_hash", table_name="control_plane_sessions")
    op.drop_table("control_plane_sessions")
    op.drop_index("ix_control_plane_users_last_authenticated", table_name="control_plane_users")
    op.drop_index("uq_control_plane_users_telegram_user_id", table_name="control_plane_users")
    op.drop_table("control_plane_users")
