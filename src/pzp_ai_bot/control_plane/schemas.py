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


class MiniAppAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    init_data: str = Field(min_length=1, max_length=16_384)


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
