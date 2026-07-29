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
