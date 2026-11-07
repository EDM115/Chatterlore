from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ControlPlaneErrorDetail(BaseModel):
    code: str
    message: str
    field_errors: dict[str, list[str]] | None = None
    request_id: str | None = None


class ControlPlaneErrorEnvelope(BaseModel):
    error: ControlPlaneErrorDetail


class ControlPlaneConflictEnvelope(BaseModel):
    error: ControlPlaneErrorDetail
    current: dict[str, object]


class MiniAppAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    init_data: str = Field(min_length=1, max_length=16_384)


class MiniAppStepUpRequest(MiniAppAuthRequest):
    action: str = Field(default="generic", min_length=1, max_length=64)


class DevelopmentStepUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=64)


class ControlPlaneUserResponse(BaseModel):
    telegram_user_id: int
    first_name: str
    last_name: str | None
    username: str | None
    language_code: str | None
    photo_url: str | None
    is_premium: bool | None


class ControlPlaneSessionResponse(BaseModel):
    user: ControlPlaneUserResponse
    client_kind: str
    authenticated_at: datetime
    step_up_at: datetime | None
    expires_at: datetime
    csrf_token: str
    capabilities: list[str]
    chat_capabilities: dict[str, list[str]]


class StepUpResponse(BaseModel):
    step_up: bool
    verified_at: datetime
    action: str


class ProviderProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    provider_kind: str
    base_url: str = Field(min_length=1, max_length=512)
    chat_model: str = Field(default="", max_length=255)
    embedding_model: str = Field(default="", max_length=255)
    chat_capable: bool = True
    embedding_capable: bool = True
    reasoning_capable: bool = False
    local: bool = False
    api_dialect: Literal["auto", "openai", "deepseek", "generic"] = "auto"
    auth_mode: Literal["api_key", "none"] = "api_key"
    local_chat_options: dict[str, Any] = Field(default_factory=dict)
    local_embedding_options: dict[str, Any] = Field(default_factory=dict)
    redact_before_ai: bool = True
    redaction_patterns: list[str] = Field(default_factory=list, max_length=100)
    fake_embedding_dimensions: int = Field(default=64, ge=8, le=65_536)


class ProviderProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    provider_kind: str | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    chat_model: str | None = Field(default=None, max_length=255)
    embedding_model: str | None = Field(default=None, max_length=255)
    chat_capable: bool | None = None
    embedding_capable: bool | None = None
    reasoning_capable: bool | None = None
    local: bool | None = None
    api_dialect: Literal["auto", "openai", "deepseek", "generic"] | None = None
    auth_mode: Literal["api_key", "none"] | None = None
    local_chat_options: dict[str, Any] | None = None
    local_embedding_options: dict[str, Any] | None = None
    redact_before_ai: bool | None = None
    redaction_patterns: list[str] | None = Field(default=None, max_length=100)
    fake_embedding_dimensions: int | None = Field(default=None, ge=8, le=65_536)
    enabled: bool | None = None


class ProviderProfileDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class ProviderProfileResponse(BaseModel):
    name: str
    provider_kind: str
    base_url: str
    chat_model: str
    embedding_model: str
    chat_capable: bool
    embedding_capable: bool
    reasoning_capable: bool
    local: bool
    api_dialect: Literal["auto", "openai", "deepseek", "generic"] = "auto"
    auth_mode: Literal["api_key", "none"] = "api_key"
    local_chat_options: dict[str, Any] = Field(default_factory=dict)
    local_embedding_options: dict[str, Any] = Field(default_factory=dict)
    redact_before_ai: bool
    redaction_patterns: list[str]
    fake_embedding_dimensions: int
    enabled: bool
    source: str
    version: int


class ProviderProfileMutationResponse(ProviderProfileResponse):
    audit_event_id: int


class ProviderCredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=131_072)


class ProviderCredentialUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    label: str | None = Field(default=None, min_length=1, max_length=64)
    position: int | None = Field(default=None, ge=0, le=10_000)
    state: str | None = None


class ProviderCredentialReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    value: str = Field(min_length=1, max_length=131_072)
    label: str | None = Field(default=None, min_length=1, max_length=64)


class ProviderCredentialDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class ProviderCredentialSummary(BaseModel):
    id: int | str
    profile_name: str
    label: str
    source: str
    environment_variable: str | None
    last_four: str | None
    fingerprint: str | None
    position: int
    state: str
    health_state: str
    cooldown_until: datetime | None
    last_error_classification: str | None
    success_count: int
    failure_count: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    version: int
    revealable: bool
    active: bool


class ProviderCredentialMutationResponse(ProviderCredentialSummary):
    audit_event_id: int


class ProviderCredentialRevealResponse(BaseModel):
    credential_id: int
    value: str
    expires_at: datetime


class ProviderPoolSummary(BaseModel):
    profile_name: str
    profile_revision: int
    active_credential_fingerprint: str | None
    active_credential_label: str | None
    health_epoch: int
    total_credentials: int
    eligible_credentials: int
    cooldown_credentials: int
    disabled_credentials: int
    last_exhausted_at: datetime | None
    last_recovered_at: datetime | None


class ProviderCredentialTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(chat|embedding)$")


class ProviderCredentialTestResponse(BaseModel):
    ok: bool
    mode: str
    model: str
    dimensions: int | None
    managed_profile_revision: int
    credential_fingerprint: str | None
    credential_label: str | None
    attempted_credential_count: int
    same_key_attempt_count: int
    rotation_count: int
    final_classification: str
    warning: str


class AdminAlertSummary(BaseModel):
    id: int
    parent_intent_id: int | None
    alert_type: str
    profile_name: str
    credential_fingerprint: str | None
    credential_label: str | None
    health_epoch: int
    recipient_telegram_id: int | None
    state: str
    attempts: int
    next_attempt_at: datetime | None
    created_at: datetime
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    error_classification: str | None


class AdminAlertPage(BaseModel):
    items: list[AdminAlertSummary]
    next_cursor: int | None
    has_more: bool


class DeleteMutationResponse(BaseModel):
    audit_event_id: int
    version: int


class BotAdminCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram_user_id: int = Field(ge=1)


class VersionedMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class BotAdminResponse(BaseModel):
    telegram_user_id: int
    source: str
    version: int | None
    granted_by: int | None
    granted_at: datetime | None


class RuntimeSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: dict[str, Any] = Field(min_length=1, max_length=1)
    version: int = Field(ge=0)


class RuntimeSettingResponse(BaseModel):
    key: str
    value: Any
    default_value: Any
    previous_value: Any
    updated_by: int | None
    updated_at: datetime | None
    version: int | None


class RuntimeSettingsMutationResponse(BaseModel):
    items: list[RuntimeSettingResponse]
    version: int
    audit_event_id: int


class ManagedChatResponse(BaseModel):
    chat_id: int
    title: str | None
    username: str | None
    status: str
    version: int
    updated_at: datetime


class ManagedChatPage(BaseModel):
    items: list[ManagedChatResponse]
    next_cursor: int | None
    has_more: bool


class ManagedChatMutationResponse(ManagedChatResponse):
    audit_event_id: int


class ManagedChatStatusRequest(VersionedMutationRequest):
    status: str = Field(pattern="^(active|paused|detached)$")


class ChatAdminCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram_user_id: int = Field(ge=1)


class ChatAdminResponse(BaseModel):
    chat_id: int
    telegram_user_id: int
    version: int
    granted_by: int
    granted_at: datetime
    active: bool


class ChatEnrollmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery: str = Field(default="mini_app", pattern="^(mini_app|telegram_dm)$")


class ChatEnrollmentResponse(BaseModel):
    request_id: int
    status: str
    delivery: str
    expires_at: datetime
    prepared_button_id: str | None
    chat_id: int | None
    error_code: str | None


class ScopeSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, int | str | bool | None] = Field(default_factory=dict, max_length=20)
    learning_enabled: bool | None = None
    allow_admins: bool | None = None
    version: int = Field(ge=0)


class ScopePolicyResponse(BaseModel):
    learning_enabled: bool
    allow_admins: bool


class ScopeSettingsResponse(BaseModel):
    scope: str
    effective: dict[str, Any]
    inherited: dict[str, Any]
    overrides: dict[str, Any]
    policy: ScopePolicyResponse
    version: int


class ScopeSettingsMutationResponse(ScopeSettingsResponse):
    audit_event_id: int


class ConfirmedChatActionRequest(VersionedMutationRequest):
    confirmation: str = Field(min_length=1, max_length=128)


class ChatForgetResponse(BaseModel):
    chat_id: int
    version: int
    messages: int
    knowledge_items: int
    embeddings: int


class ProviderCatalogLocalState(BaseModel):
    dependency_available: bool
    model_loaded: bool
    chat_dependency_available: bool = False
    embedding_dependency_available: bool = False
    chat_model_loaded: bool = False
    embedding_model_loaded: bool = False
    server_configured: bool = False


class ProviderCatalogPoolState(BaseModel):
    eligible: int
    total: int


class ProviderCatalogProfile(BaseModel):
    name: str
    provider_kind: str
    source: str
    enabled: bool
    chat_model: str
    embedding_model: str
    local: bool = False
    api_dialect: str = "auto"
    auth_mode: str = "api_key"
    capabilities: list[str]
    availability: str
    local_state: ProviderCatalogLocalState | None
    pool: ProviderCatalogPoolState | None


class QueryCatalogResponse(BaseModel):
    scope: str
    chat_models: list[str]
    embedding_models: list[str]
    reasoning_levels: list[str]
    profiles: list[ProviderCatalogProfile]


class QueryRequestOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=128)
    memory_profile: str | None = Field(default=None, min_length=1, max_length=64)
    provider_profile: str | None = Field(default=None, min_length=1, max_length=64)
    retrieval_provider_profile: str | None = Field(default=None, min_length=1, max_length=64)
    chat_provider_profile: str | None = Field(default=None, min_length=1, max_length=64)
    chat_model: str | None = Field(default=None, min_length=1, max_length=255)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=255)
    reasoning_level: str | None = Field(default=None, min_length=1, max_length=32)
    top_k: int = Field(default=6, ge=1, le=20)


class QuerySearchRequest(QueryRequestOptions):
    query: str = Field(min_length=1, max_length=8_000)


class QueryAskRequest(QueryRequestOptions):
    question: str = Field(min_length=1, max_length=8_000)


class QuerySelectionResponse(BaseModel):
    retrieval_provider_profile: str
    embedding_model: str
    chat_provider_profile: str | None
    chat_model: str | None
    reasoning_level: str | None


class QueryResultItemResponse(BaseModel):
    content: str
    score: float
    source: str
    scope: str
    source_message_ids: list[int]


class QuerySearchResponse(BaseModel):
    destination_scope: str
    source_scopes: list[str]
    memory_profile: str | None
    selection: QuerySelectionResponse
    results: list[QueryResultItemResponse]
    elapsed_ms: int


class QueryAskResponse(QuerySearchResponse):
    answer: str
    insufficient_context: bool


class MemoryItemResponse(BaseModel):
    id: int
    scope: str
    kind: str
    content: str
    source_message_ids: list[int]
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MemoryItemPage(BaseModel):
    items: list[MemoryItemResponse]
    next_cursor: int | None
    has_more: bool


class MemoryRawSourceResponse(BaseModel):
    telegram_message_id: int
    user_id: int | None
    username: str | None
    text: str
    created_at: datetime
    message_permalink: str | None


class MemoryRawSourcesResponse(BaseModel):
    item_id: int
    scope: str
    sources: list[MemoryRawSourceResponse]


class MemoryItemRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="embed", pattern="^(embed|summarize|both)$")


class MemoryItemRefreshResponse(BaseModel):
    id: int
    scope: str
    kind: str
    mode: str
    summarized: bool
    embeddings_refreshed: int


class MemoryReviewResponse(BaseModel):
    id: int
    scope: str
    kind: str
    content: str
    source_message_ids: list[int]
    created_at: datetime


class MemoryReviewPage(BaseModel):
    items: list[MemoryReviewResponse]
    next_cursor: int | None
    has_more: bool


class MemoryReviewMutationResponse(BaseModel):
    review_id: int
    status: str
    knowledge_item_id: int | None = None


class StatsMemoryResponse(BaseModel):
    messages: int
    learned: int
    skipped: int
    knowledge_items: int
    embeddings: int
    pending_reviews: int
    audit_events: int


class StatsAIUsageResponse(BaseModel):
    total: int
    errors: int
    average_latency_ms: int | None


class StatsStorageResponse(BaseModel):
    database_bytes: int
    memory_bytes: int
    memory_files: int
    memory_scan_truncated: bool


class SchedulerTaskResponse(BaseModel):
    name: str
    state: str
    healthy: bool
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_exception_type: str | None
    consecutive_failures: int


class StatsRuntimeResponse(BaseModel):
    uptime_seconds: int
    schema_revision: str
    scheduler: list[SchedulerTaskResponse]


class StatsAdministrationResponse(BaseModel):
    bot_admins: int
    delegated_chat_admins: int
    managed_chats: dict[str, int]


class StatsCredentialResponse(BaseModel):
    eligible: int
    total: int


class StatsProviderResponse(BaseModel):
    profiles: int
    credentials: StatsCredentialResponse
    local_dependency_available: bool
    local_models_loaded: int


class StatsAlertResponse(BaseModel):
    states: dict[str, int]


class StatsOverviewResponse(BaseModel):
    scope: str
    generated_at: datetime
    memory: StatsMemoryResponse
    ai_usage: StatsAIUsageResponse
    storage: StatsStorageResponse
    runtime: StatsRuntimeResponse
    administration: StatsAdministrationResponse
    providers: StatsProviderResponse
    alerts: StatsAlertResponse


class StatsTimeseriesPointResponse(BaseModel):
    date: date
    messages: int
    learned: int
    skipped: int
    knowledge_items: int
    ai_calls: int
    ai_errors: int


class StatsTimeseriesResponse(BaseModel):
    scope: str
    days: int
    points: list[StatsTimeseriesPointResponse]


class AuditEventResponse(BaseModel):
    id: int
    event_type: str
    scope: str | None
    user_id: int | None
    payload: dict[str, Any]
    created_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventResponse]
    next_cursor: int | None
    has_more: bool


class AIUsageResponse(BaseModel):
    id: int
    scope: str | None
    provider: str
    model: str
    operation: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    error: str | None
    created_at: datetime


class AIUsagePage(BaseModel):
    items: list[AIUsageResponse]
    next_cursor: int | None
    has_more: bool


class OperationsHealthResponse(BaseModel):
    generated_at: datetime
    checks: list[str]
    provider_calls_performed: bool
    runtime: dict[str, Any]
    storage: dict[str, Any]
    providers: dict[str, Any]
    alerts: dict[str, Any]


class OperationCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str | None = Field(default=None, min_length=1, max_length=64)
    mode: str | None = Field(default=None, pattern="^(chat|embedding)$")
    allow_download: bool = False


class OperationCheckResponse(BaseModel):
    check: str
    ok: bool
    result: str
    details: dict[str, Any]
    warning: str | None = None
    elapsed_ms: int | None = None
