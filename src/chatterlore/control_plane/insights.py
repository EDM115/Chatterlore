from __future__ import annotations

import importlib.util
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.config import Settings
from chatterlore.db.repos.insights import InsightRepository
from chatterlore.db.session import session_scope
from chatterlore.memory.scope import Scope
from chatterlore.telegram.admin import AdminService

from .secrets.service import ProviderSecretService


class ControlPlaneInsightsService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        admins: AdminService,
        secrets: ProviderSecretService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.admins = admins
        self.secrets = secrets
        self.started_monotonic = time.monotonic()

    async def overview(self, scope: Scope | None) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            repository = InsightRepository(session)
            scoped = await repository.scoped_overview(scope)
            system = await repository.system_counts()
        profiles = await self.secrets.list_profiles()
        local_profiles = [
            profile for profile in profiles if profile.local or profile.provider_kind == "local"
        ]
        local_models_loaded = 0
        for profile in local_profiles:
            try:
                selection = self.secrets.registry.select(
                    profile.name,
                    chat_model=profile.chat_model,
                    embedding_model=profile.embedding_model,
                )
            except ValueError:
                continue
            if bool(getattr(selection.embedding_provider, "loaded", False)):
                local_models_loaded += 1
        memory_size = bounded_directory_size(self.settings.memory_dir)
        sqlite_path = self.settings.sqlite_path
        return {
            "scope": "all:main" if scope is None else scope.key,
            "generated_at": datetime.now(UTC),
            "memory": {
                "messages": scoped.messages,
                "learned": scoped.learned,
                "skipped": scoped.skipped,
                "knowledge_items": scoped.knowledge_items,
                "embeddings": scoped.embeddings,
                "pending_reviews": scoped.pending_reviews,
                "audit_events": scoped.audit_events,
            },
            "ai_usage": {
                "total": scoped.ai_total,
                "errors": scoped.ai_errors,
                "average_latency_ms": scoped.ai_average_latency_ms,
            },
            "storage": {
                "database_bytes": (
                    sqlite_path.stat().st_size
                    if sqlite_path is not None and sqlite_path.exists()
                    else 0
                ),
                "memory_bytes": memory_size[0],
                "memory_files": memory_size[1],
                "memory_scan_truncated": memory_size[2],
            },
            "runtime": {
                "uptime_seconds": max(0, int(time.monotonic() - self.started_monotonic)),
                "schema_revision": system["schema_revision"],
            },
            "administration": {
                "bot_admins": len(self.admins.effective_bot_admin_ids),
                "delegated_chat_admins": system["delegated_chat_admins"],
                "managed_chats": system["managed_chats"],
            },
            "providers": {
                "profiles": len(profiles),
                "credentials": system["credentials"],
                "local_dependency_available": (
                    importlib.util.find_spec("sentence_transformers") is not None
                ),
                "local_models_loaded": local_models_loaded,
            },
            "alerts": {"states": system["alerts"]},
        }

    async def timeseries(self, scope: Scope | None, *, days: int) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            points = await InsightRepository(session).timeseries(scope, days=days)
        return {
            "scope": "all:main" if scope is None else scope.key,
            "days": days,
            "points": [
                {
                    "date": point.day,
                    "messages": point.messages,
                    "learned": point.learned,
                    "skipped": point.skipped,
                    "knowledge_items": point.knowledge_items,
                    "ai_calls": point.ai_calls,
                    "ai_errors": point.ai_errors,
                }
                for point in points
            ],
        }


def bounded_directory_size(root: Path, *, max_entries: int = 10_000) -> tuple[int, int, bool]:
    if not root.exists():
        return 0, 0, False
    total_bytes = 0
    files = 0
    visited = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                visited += 1
                if visited > max_entries:
                    return total_bytes, files, True
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total_bytes += entry.stat(follow_symlinks=False).st_size
                        files += 1
                except OSError:
                    continue
    return total_bytes, files, False
