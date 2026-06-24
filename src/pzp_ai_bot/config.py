from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderKind = Literal["fake", "openai_compatible", "local"]


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    name: str
    provider: ProviderKind
    chat_model: str
    embedding_model: str
    openai_api_key: str
    openai_base_url: str
    redact_before_ai: bool
    redaction_custom_patterns: tuple[str, ...]
    fake_embedding_dimensions: int


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    bot_admins: str = ""
    runtime_mode: Literal["polling", "webhook"] = "polling"

    webhook_url: str = ""
    webhook_secret: str = "change-me"  # noqa: S105
    webapp_host: str = "0.0.0.0"  # noqa: S104
    webapp_port: int = 8080

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
    raw_message_retention_days: int | None = Field(default=None, ge=1)
    knowledge_retention_days: int | None = Field(default=None, ge=1)
    review_before_compact: bool = False
    fake_embedding_dimensions: int = Field(default=64, ge=8)

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
                provider=cast(ProviderKind, provider),
                chat_model=str(raw_profile.get("chat_model") or default.chat_model),
                embedding_model=str(raw_profile.get("embedding_model") or default.embedding_model),
                openai_api_key=api_key or default.openai_api_key,
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
