from __future__ import annotations

from datetime import datetime

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
    chat_model: str = Field(min_length=1, max_length=255)
    embedding_model: str = Field(min_length=1, max_length=255)
    chat_capable: bool = True
    embedding_capable: bool = True
    reasoning_capable: bool = False
    local: bool = False
    redact_before_ai: bool = True
    redaction_patterns: list[str] = Field(default_factory=list, max_length=100)
    fake_embedding_dimensions: int = Field(default=64, ge=8, le=65_536)


class ProviderProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    provider_kind: str | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    chat_model: str | None = Field(default=None, min_length=1, max_length=255)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=255)
    chat_capable: bool | None = None
    embedding_capable: bool | None = None
    reasoning_capable: bool | None = None
    local: bool | None = None
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
