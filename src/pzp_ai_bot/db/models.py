from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


MESSAGE_MAIN_THREAD_SENTINEL = -1


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "chat_id", "message_thread_id", "telegram_message_id", name="uq_msg_scope"
        ),
        Index(
            "uq_messages_logical_scope",
            "chat_id",
            text(f"COALESCE(message_thread_id, {MESSAGE_MAIN_THREAD_SENTINEL})"),
            "telegram_message_id",
            unique=True,
        ),
        Index("ix_messages_scope_created", "chat_id", "message_thread_id", "created_at"),
        Index(
            "ix_messages_summary_claim",
            "learned",
            "summarized_at",
            "chat_id",
            "message_thread_id",
            "summary_claimed_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topic_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_permalink: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    learned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        Index("ix_knowledge_scope_created", "chat_id", "message_thread_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CanonicalScopeSnapshot(Base):
    __tablename__ = "canonical_scope_snapshots"
    __table_args__ = (UniqueConstraint("scope_key", name="uq_canonical_scope_snapshots_scope_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    entries_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_profile: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    reasoning_level: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index("ix_embeddings_target", "target_type", "target_id"),
        Index("ix_embeddings_scope", "chat_id", "message_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningPolicy(Base):
    __tablename__ = "learning_policies"
    __table_args__ = (
        UniqueConstraint(
            "policy_type",
            "chat_id",
            "message_thread_id",
            "user_id",
            "key",
            name="uq_learning_policy",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScopeMetadata(Base):
    __tablename__ = "scope_metadata"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_thread_id", name="uq_scope_metadata"),
        Index("ix_scope_metadata_scope", "chat_id", "message_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topic_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chat_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScopeSettings(Base):
    __tablename__ = "scope_settings"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_thread_id", name="uq_scope_settings"),
        Index("ix_scope_settings_scope", "chat_id", "message_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_top_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_batch_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_prompt_template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reasoning_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_message_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    knowledge_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_before_compact: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemoryReviewItem(Base):
    __tablename__ = "memory_review_items"
    __table_args__ = (
        Index("ix_memory_review_scope_status", "chat_id", "message_thread_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryProfile(Base):
    __tablename__ = "memory_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_memory_profiles_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemoryProfileScope(Base):
    __tablename__ = "memory_profile_scopes"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "chat_id", "message_thread_id", name="uq_memory_profile_scope"
        ),
        Index("ix_memory_profile_scopes_profile", "profile_id"),
        Index("ix_memory_profile_scopes_scope", "chat_id", "message_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("memory_profiles.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MemoryScopePermission(Base):
    __tablename__ = "memory_scope_permissions"
    __table_args__ = (
        UniqueConstraint(
            "destination_chat_id",
            "destination_thread_id",
            "source_chat_id",
            "source_thread_id",
            name="uq_memory_scope_permission",
        ),
        Index(
            "ix_memory_scope_permissions_destination",
            "destination_chat_id",
            "destination_thread_id",
        ),
        Index("ix_memory_scope_permissions_source", "source_chat_id", "source_thread_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    destination_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    can_query: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SummaryTemplate(Base):
    __tablename__ = "summary_templates"
    __table_args__ = (UniqueConstraint("name", name="uq_summary_templates_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemPromptTemplate(Base):
    __tablename__ = "system_prompt_templates"
    __table_args__ = (UniqueConstraint("name", name="uq_system_prompt_templates_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_runtime_settings_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ForgetChallenge(Base):
    __tablename__ = "forget_challenges"
    __table_args__ = (
        Index(
            "ix_forget_challenges_scope_user",
            "chat_id",
            "message_thread_id",
            "user_id",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SettingsChallenge(Base):
    __tablename__ = "settings_challenges"
    __table_args__ = (
        Index(
            "ix_settings_challenges_scope_user",
            "chat_id",
            "message_thread_id",
            "user_id",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    updates: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BotEvent(Base):
    __tablename__ = "bot_events"
    __table_args__ = (
        Index(
            "ix_bot_events_type_scope_created_at",
            "event_type",
            "chat_id",
            "message_thread_id",
            "created_at",
        ),
        Index("ix_bot_events_scope_created_at", "chat_id", "message_thread_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ix_ai_usage_scope_created_at", "chat_id", "message_thread_id", "created_at"),
        Index("ix_ai_usage_operation_created_at", "operation", "created_at"),
        Index("ix_ai_usage_error_created_at", "error", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ControlPlaneUser(Base):
    __tablename__ = "control_plane_users"
    __table_args__ = (
        Index("uq_control_plane_users_telegram_user_id", "telegram_user_id", unique=True),
        Index("ix_control_plane_users_last_authenticated", "last_authenticated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_premium: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_authenticated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ControlPlaneSession(Base):
    __tablename__ = "control_plane_sessions"
    __table_args__ = (
        Index("uq_control_plane_sessions_token_hash", "token_hash", unique=True),
        Index(
            "ix_control_plane_sessions_user_active", "user_id", "revoked_at", "absolute_expires_at"
        ),
        Index("ix_control_plane_sessions_expiry", "idle_expires_at", "absolute_expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("control_plane_users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    client_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    step_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    step_up_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OIDCAuthTransaction(Base):
    __tablename__ = "oidc_auth_transactions"
    __table_args__ = (
        Index("uq_oidc_auth_transactions_state_hash", "state_hash", unique=True),
        Index("ix_oidc_auth_transactions_expiry", "expires_at", "consumed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pkce_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pkce_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    oidc_nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    return_to: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BotAdminGrant(Base):
    __tablename__ = "bot_admin_grants"
    __table_args__ = (
        Index("uq_bot_admin_grants_telegram_user_id", "telegram_user_id", unique=True),
        Index("ix_bot_admin_grants_active", "revoked_at", "telegram_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ChatAdminGrant(Base):
    __tablename__ = "chat_admin_grants"
    __table_args__ = (
        Index("uq_chat_admin_grants_scope_user", "chat_id", "telegram_user_id", unique=True),
        Index("ix_chat_admin_grants_user_active", "telegram_user_id", "revoked_at", "chat_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ManagedChat(Base):
    __tablename__ = "managed_chats"
    __table_args__ = (
        Index("uq_managed_chats_chat_id", "chat_id", unique=True),
        Index("ix_managed_chats_status", "status", "updated_at"),
        Index("ix_managed_chats_enrollment_expiry", "enrollment_expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    enrollment_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AIProviderProfile(Base):
    __tablename__ = "ai_provider_profiles"
    __table_args__ = (
        Index("uq_ai_provider_profiles_name", "name", unique=True),
        Index("ix_ai_provider_profiles_enabled", "enabled", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    chat_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_capable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    embedding_capable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasoning_capable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    local: Mapped[bool] = mapped_column(Boolean, nullable=False)
    redact_before_ai: Mapped[bool] = mapped_column(Boolean, nullable=False)
    redaction_patterns: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fake_embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="managed")
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AIProviderCredential(Base):
    __tablename__ = "ai_provider_credentials"
    __table_args__ = (
        Index("ix_ai_provider_credentials_profile_position", "profile_id", "position", "id"),
        Index(
            "ix_ai_provider_credentials_profile_state",
            "profile_id",
            "lifecycle_state",
            "cooldown_until",
        ),
        Index("ix_ai_provider_credentials_fingerprint", "profile_id", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    environment_variable: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wrap_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    master_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_four: Mapped[str | None] = mapped_column(String(16), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    health_state: Mapped[str] = mapped_column(String(32), nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AIProviderPoolState(Base):
    __tablename__ = "ai_provider_pool_state"
    __table_args__ = (
        Index("uq_ai_provider_pool_state_profile_id", "profile_id", unique=True),
        Index("ix_ai_provider_pool_state_active", "active_credential_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_provider_profiles.id", ondelete="CASCADE"), nullable=False
    )
    active_credential_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ai_provider_credentials.id", ondelete="SET NULL"), nullable=True
    )
    health_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_exhausted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_recovered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
