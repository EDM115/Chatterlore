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
    cooldown_until: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    version: int
    revealable: bool


class ProviderCredentialMutationResponse(ProviderCredentialSummary):
    audit_event_id: int


class ProviderCredentialRevealResponse(BaseModel):
    credential_id: int
    value: str
    expires_at: datetime


class DeleteMutationResponse(BaseModel):
    audit_event_id: int
    version: int
