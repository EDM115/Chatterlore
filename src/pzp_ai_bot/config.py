from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    ai_provider: Literal["fake", "openai_compatible", "local"] = "fake"
    chat_model: str = "fake-chat"
    embedding_model: str = "fake-embedding"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    learning_default_enabled: bool = False
    redact_before_ai: bool = True
    summary_batch_size: int = Field(default=25, ge=1)
    summary_idle_seconds: int = Field(default=900, ge=1)
    retrieval_top_k: int = Field(default=6, ge=1)
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
