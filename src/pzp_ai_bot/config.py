from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderKind = Literal["fake", "openai_compatible", "local"]


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    name: str
    provider: ProviderKind
    chat_model: str
    embedding_model: str
    openai_api_key: str
    openai_api_key_env: str | None
    openai_base_url: str
    redact_before_ai: bool
    redaction_custom_patterns: tuple[str, ...]
    fake_embedding_dimensions: int


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    telegram_bot_token: str = ""
    bot_admins: str = ""
    runtime_mode: Literal["polling", "webhook"] = "polling"

    webhook_url: str = ""
    webhook_secret: str = "change-me"  # noqa: S105
    webapp_host: str = "0.0.0.0"  # noqa: S104
    webapp_port: int = 8080

    control_plane_enabled: bool = False
    control_plane_public_origin: str = ""
    telegram_oidc_client_id: str = ""
    telegram_oidc_client_secret: str = ""
    control_plane_auth_max_age_seconds: int = Field(default=300, ge=1)
    control_plane_step_up_max_age_seconds: int = Field(default=300, ge=1)
    control_plane_oidc_transaction_ttl_seconds: int = Field(default=600, ge=1)
    control_plane_session_idle_seconds: int = Field(default=43_200, ge=60)
    control_plane_session_absolute_seconds: int = Field(default=604_800, ge=60)
    control_plane_master_keys: str = ""
    control_plane_active_master_key_id: str = ""
    control_plane_rate_limit_window_seconds: int = Field(default=60, ge=1)
    control_plane_auth_start_rate_limit: int = Field(default=10, ge=1)
    control_plane_auth_callback_rate_limit: int = Field(default=20, ge=1)
    control_plane_mini_app_rate_limit: int = Field(default=20, ge=1)
    control_plane_step_up_rate_limit: int = Field(default=10, ge=1)
    control_plane_provider_test_rate_limit: int = Field(default=5, ge=1)
    control_plane_max_managed_profiles: int = Field(default=32, ge=1, le=256)
    control_plane_max_credentials_per_profile: int = Field(default=10, ge=1, le=100)
    control_plane_max_secret_length: int = Field(default=16_384, ge=1, le=131_072)

    db_url: str = "sqlite+aiosqlite:///data/bot.db"
    memory_dir: Path = Path("data/memory")

    ai_provider: ProviderKind = "fake"
    ai_provider_profile: str = "default"
    ai_provider_profiles: str = ""
    chat_model: str = "fake-chat"
    embedding_model: str = "fake-embedding"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    provider_retry_attempts: int = Field(default=3, ge=1)
    provider_retry_backoff_seconds: float = Field(default=0.5, ge=0.0)
    control_plane_alert_delivery_interval_seconds: int = Field(default=5, ge=1, le=3_600)
    log_level: str = "INFO"

    learning_default_enabled: bool = False
    redact_before_ai: bool = True
    redaction_custom_patterns: str = ""
    answer_min_score: float = Field(default=0.05, ge=0.0)
    system_prompt_template: str = "scoped"
    reasoning_level: Literal["low", "medium", "high"] = "medium"
    allowed_chat_models: str = ""
    allowed_embedding_models: str = ""
    allowed_reasoning_levels: str = "low,medium,high"
    summary_batch_size: int = Field(default=25, ge=1)
    summary_idle_seconds: int = Field(default=900, ge=1)
    runtime_settings_refresh_seconds: int = Field(default=60, ge=1)
    admin_cache_ttl_seconds: int = Field(default=300, ge=0)
    forget_confirm_ttl_seconds: int = Field(default=600, ge=1)
    retrieval_top_k: int = Field(default=6, ge=1)
    retrieval_candidate_limit: int = Field(default=5_000, ge=1)
    raw_message_retention_days: int | None = Field(default=None, ge=1)
    knowledge_retention_days: int | None = Field(default=None, ge=1)
    review_before_compact: bool = False
    fake_embedding_dimensions: int = Field(default=64, ge=8)

    @model_validator(mode="after")
    def validate_control_plane_bootstrap(self) -> Settings:
        origin = self.control_plane_public_origin.strip().rstrip("/")
        if origin:
            parts = urlsplit(origin)
            if (
                parts.scheme != "https"
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.query
                or parts.fragment
                or parts.path not in {"", "/"}
            ):
                raise ValueError("control plane requires an HTTPS public origin without a path")
            self.control_plane_public_origin = origin
        if self.control_plane_session_idle_seconds > self.control_plane_session_absolute_seconds:
            raise ValueError("control plane session idle TTL cannot exceed absolute TTL")
        key_ids = self.control_plane_master_key_ids
        if (
            self.control_plane_active_master_key_id
            and self.control_plane_active_master_key_id not in key_ids
        ):
            raise ValueError("active control-plane master key ID is not present in the key map")
        if not self.control_plane_enabled:
            return self
        if not origin:
            raise ValueError("control plane requires an HTTPS public origin")
        if not self.telegram_bot_token.strip():
            raise ValueError("control plane requires a Telegram bot token")
        if not self.telegram_oidc_client_id.strip():
            raise ValueError("control plane requires a Telegram OIDC client ID")
        if not self.telegram_oidc_client_secret.strip():
            raise ValueError("control plane requires a Telegram OIDC client secret")
        if not key_ids:
            raise ValueError("control plane requires a master key map")
        if not self.control_plane_active_master_key_id:
            raise ValueError("control plane requires an active master key ID")
        return self

    @property
    def bot_admin_ids(self) -> set[int]:
        ids: set[int] = set()
        for raw in self.bot_admins.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ids.add(int(raw))
            except ValueError:
                continue
        return ids

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite+aiosqlite:///"
        if not self.db_url.startswith(prefix):
            return None
        return Path(self.db_url.removeprefix(prefix))

    @property
    def control_plane_callback_url(self) -> str:
        if not self.control_plane_public_origin:
            return ""
        return f"{self.control_plane_public_origin}/auth/telegram/callback"

    @property
    def control_plane_master_key_bytes(self) -> dict[str, bytes]:
        if not self.control_plane_master_keys.strip():
            return {}
        try:
            payload = json.loads(self.control_plane_master_keys)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CONTROL_PLANE_MASTER_KEYS must be JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("CONTROL_PLANE_MASTER_KEYS must be a JSON object")
        keys: dict[str, bytes] = {}
        for raw_key_id, raw_value in payload.items():
            key_id = str(raw_key_id).strip()
            value = str(raw_value).strip()
            if not key_id or not value:
                raise ValueError("CONTROL_PLANE_MASTER_KEYS cannot contain empty IDs or values")
            try:
                material = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(
                    f"CONTROL_PLANE_MASTER_KEYS entry {key_id!r} must be valid base64"
                ) from exc
            if len(material) != 32:
                raise ValueError(
                    f"CONTROL_PLANE_MASTER_KEYS entry {key_id!r} must decode to 32 bytes"
                )
            keys[key_id] = material
        return keys

    @property
    def control_plane_master_key_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.control_plane_master_key_bytes))

    @property
    def control_plane_safe_config(self) -> dict[str, Any]:
        return {
            "enabled": self.control_plane_enabled,
            "public_origin": self.control_plane_public_origin,
            "callback_url": self.control_plane_callback_url,
            "oidc_client_configured": bool(
                self.telegram_oidc_client_id and self.telegram_oidc_client_secret
            ),
            "master_key_ids": list(self.control_plane_master_key_ids),
            "active_master_key_id": self.control_plane_active_master_key_id,
            "auth_max_age_seconds": self.control_plane_auth_max_age_seconds,
            "step_up_max_age_seconds": self.control_plane_step_up_max_age_seconds,
            "session_idle_seconds": self.control_plane_session_idle_seconds,
            "session_absolute_seconds": self.control_plane_session_absolute_seconds,
        }

    @property
    def custom_redaction_patterns(self) -> tuple[str, ...]:
        return tuple(
            part.strip()
            for part in self.redaction_custom_patterns.replace("||", "\n").splitlines()
            if part.strip()
        )

    @property
    def allowed_chat_model_names(self) -> set[str]:
        return _csv_set(self.allowed_chat_models)

    @property
    def allowed_embedding_model_names(self) -> set[str]:
        return _csv_set(self.allowed_embedding_models)

    @property
    def allowed_reasoning_level_names(self) -> set[str]:
        values = _csv_set(self.allowed_reasoning_levels)
        return values or {"low", "medium", "high"}

    @property
    def provider_profiles(self) -> dict[str, ProviderProfile]:
        default = ProviderProfile(
            name="default",
            provider=self.ai_provider,
            chat_model=self.chat_model,
            embedding_model=self.embedding_model,
            openai_api_key=self.openai_api_key,
            openai_api_key_env="OPENAI_API_KEY" if self.openai_api_key else None,
            openai_base_url=self.openai_base_url,
            redact_before_ai=self.redact_before_ai,
            redaction_custom_patterns=self.custom_redaction_patterns,
            fake_embedding_dimensions=self.fake_embedding_dimensions,
        )
        profiles = {default.name: default}
        if not self.ai_provider_profiles.strip():
            return profiles
        payload = _load_profile_payload(self.ai_provider_profiles)
        for raw_name, raw_profile in payload.items():
            if not isinstance(raw_profile, dict):
                raise ValueError(f"provider profile {raw_name!r} must be an object")
            name = str(raw_profile.get("name") or raw_name).strip().lower()
            if not name:
                raise ValueError("provider profile name cannot be empty")
            provider = str(
                raw_profile.get("provider") or raw_profile.get("ai_provider") or default.provider
            )
            if provider not in {"fake", "openai_compatible", "local"}:
                raise ValueError(f"provider profile {name!r} has unsupported provider {provider!r}")
            api_key = str(raw_profile.get("openai_api_key") or raw_profile.get("api_key") or "")
            api_key_env = str(
                raw_profile.get("openai_api_key_env") or raw_profile.get("api_key_env") or ""
            )
            if api_key_env:
                api_key = os.getenv(api_key_env, api_key)
            patterns = raw_profile.get("redaction_custom_patterns") or raw_profile.get(
                "redaction_patterns"
            )
            profiles[name] = ProviderProfile(
                name=name,
                provider=provider,
                chat_model=str(raw_profile.get("chat_model") or default.chat_model),
                embedding_model=str(raw_profile.get("embedding_model") or default.embedding_model),
                openai_api_key=api_key or default.openai_api_key,
                openai_api_key_env=api_key_env or default.openai_api_key_env,
                openai_base_url=str(
                    raw_profile.get("openai_base_url")
                    or raw_profile.get("base_url")
                    or default.openai_base_url
                ),
                redact_before_ai=bool(
                    raw_profile.get("redact_before_ai", default.redact_before_ai)
                ),
                redaction_custom_patterns=_patterns_from_value(patterns)
                or default.redaction_custom_patterns,
                fake_embedding_dimensions=int(
                    raw_profile.get("fake_embedding_dimensions")
                    or default.fake_embedding_dimensions
                ),
            )
        return profiles

    def provider_profile_by_name(self, name: str | None) -> ProviderProfile:
        selected = (name or self.ai_provider_profile or "default").strip().lower()
        profiles = self.provider_profiles
        if selected not in profiles:
            available = ", ".join(sorted(profiles))
            raise ValueError(
                f"provider profile {selected!r} was not found. Available profiles: {available}"
            )
        return profiles[selected]

    def validate_chat_model(self, model: str) -> str:
        clean = model.strip()
        if not clean:
            raise ValueError("chat_model cannot be empty")
        allowed = self.allowed_chat_model_names
        if allowed and clean not in allowed:
            raise ValueError(f"chat_model {clean!r} is not in allowed_chat_models")
        return clean

    def validate_embedding_model(self, model: str) -> str:
        clean = model.strip()
        if not clean:
            raise ValueError("embedding_model cannot be empty")
        allowed = self.allowed_embedding_model_names
        if allowed and clean not in allowed:
            raise ValueError(f"embedding_model {clean!r} is not in allowed_embedding_models")
        return clean

    def validate_reasoning_level(self, level: str) -> str:
        clean = level.strip().lower()
        if clean not in self.allowed_reasoning_level_names:
            raise ValueError(f"reasoning_level {clean!r} is not in allowed_reasoning_levels")
        return clean


def _csv_set(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _patterns_from_value(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(
            part.strip() for part in value.replace("||", "\n").splitlines() if part.strip()
        )
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    raise ValueError("redaction patterns must be a string or list")


def _load_profile_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI_PROVIDER_PROFILES must be JSON: {exc.msg}") from exc
    if isinstance(payload, list):
        out: dict[str, Any] = {}
        for item in payload:
            if not isinstance(item, dict) or "name" not in item:
                raise ValueError("AI_PROVIDER_PROFILES list items must be objects with a name")
            out[str(item["name"])] = item
        return out
    if not isinstance(payload, dict):
        raise ValueError("AI_PROVIDER_PROFILES must be a JSON object or list")
    return payload


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
