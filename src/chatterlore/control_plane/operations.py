from __future__ import annotations

import importlib.util
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.ai.protocols import ChatMessage
from chatterlore.config import Settings
from chatterlore.db.repos.events import EventRepository
from chatterlore.db.session import session_scope
from chatterlore.memory.provider_operations import ProviderOperations
from chatterlore.memory.service import MemoryService

from .insights import ControlPlaneInsightsService, bounded_directory_size
from .secrets.service import ProviderSecretService


class ControlPlaneOperationsService:
    CHECKS = ("db-integrity", "local-model-readiness", "projection-status", "provider-profile")

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        memory: MemoryService,
        secrets: ProviderSecretService,
        insights: ControlPlaneInsightsService,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.memory = memory
        self.secrets = secrets
        self.insights = insights
        self.provider_operations = ProviderOperations()

    async def health(self) -> dict[str, Any]:
        overview = await self.insights.overview(None)
        return {
            "generated_at": datetime.now(UTC),
            "checks": list(self.CHECKS),
            "provider_calls_performed": False,
            "runtime": overview["runtime"],
            "storage": overview["storage"],
            "providers": overview["providers"],
            "alerts": overview["alerts"],
        }

    async def run_check(
        self,
        name: str,
        *,
        actor_user_id: int,
        profile_name: str | None = None,
        mode: str | None = None,
        allow_download: bool = False,
    ) -> dict[str, Any]:
        if name == "db-integrity":
            result = await self._db_integrity()
        elif name == "projection-status":
            result = self._projection_status()
        elif name == "provider-profile":
            if profile_name is None or mode is None:
                raise ValueError("provider profile and mode are required")
            result = await self._provider_profile(profile_name, mode)
        elif name == "local-model-readiness":
            if profile_name is None:
                raise ValueError("local provider profile is required")
            result = await self._local_readiness(profile_name, allow_download=allow_download)
        else:
            raise ValueError("operation check is invalid")
        async with session_scope(self.session_factory) as session:
            await EventRepository(session).add(
                event_type="control_plane.operations.check",
                user_id=actor_user_id,
                payload={
                    "check": name,
                    "ok": bool(result["ok"]),
                    "profile_name": profile_name,
                    "mode": mode,
                    "download_attempted": bool(allow_download),
                },
            )
        return {"check": name, **result}

    async def _db_integrity(self) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            value = await session.scalar(text("PRAGMA integrity_check"))
        clean = str(value or "unknown")
        return {"ok": clean.lower() == "ok", "result": clean, "details": {}}

    def _projection_status(self) -> dict[str, Any]:
        _size, files, truncated = bounded_directory_size(self.settings.memory_dir)
        main_exists = self.memory.markdown.main_path.exists()
        return {
            "ok": main_exists and not truncated,
            "result": "ready" if main_exists and not truncated else "attention_required",
            "details": {
                "main_projection_exists": main_exists,
                "memory_files": files,
                "scan_truncated": truncated,
            },
        }

    async def _provider_profile(self, profile_name: str, mode: str) -> dict[str, Any]:
        clean_mode = mode.strip().lower()
        if clean_mode not in {"chat", "embedding"}:
            raise ValueError("provider test mode is invalid")
        capabilities = self.secrets.registry.profile_capabilities(profile_name)
        if clean_mode not in capabilities:
            raise ValueError(f"provider profile is not {clean_mode} capable")
        selection = self.secrets.registry.select(profile_name)
        started = time.perf_counter()
        if clean_mode == "chat":
            execution = await self.provider_operations.execute_chat(
                selection,
                [ChatMessage(role="user", content="Reply with exactly OK.")],
                operation="manual_provider_test",
                metadata={"provider_profile": selection.profile.name, "mode": clean_mode},
                temperature=0.0,
                max_tokens=8,
                reasoning_effort=(
                    "medium"
                    if "reasoning" in self.secrets.registry.profile_capabilities(profile_name)
                    else None
                ),
            )
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_usage(session, execution.telemetry)
            ok = execution.failure is None
            details = {
                "profile_name": selection.profile.name,
                "mode": clean_mode,
                "model": selection.chat_model,
                "latency_ms": execution.telemetry.latency_ms,
            }
        else:
            generation = await self.provider_operations.generate_embeddings(
                selection,
                ["chatterlore provider readiness check"],
                operation="manual_provider_test",
                metadata={"provider_profile": selection.profile.name, "mode": clean_mode},
            )
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_embedding_usage(
                    session,
                    selection=selection,
                    generation=generation,
                    texts_count=1,
                    operation="manual_provider_test",
                    metadata={"provider_profile": selection.profile.name, "mode": clean_mode},
                )
            ok = generation.failure is None
            details = {
                "profile_name": selection.profile.name,
                "mode": clean_mode,
                "model": selection.embedding_model,
                "dimensions": generation.dimensions,
                "latency_ms": generation.telemetry.latency_ms,
            }
        return {
            "ok": ok,
            "result": "ready" if ok else "provider_failed",
            "details": details,
            "warning": "This explicit manual check may incur provider usage charges.",
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    async def _local_readiness(self, profile_name: str, *, allow_download: bool) -> dict[str, Any]:
        profile = self.secrets.registry.profile_by_name(profile_name)
        if profile.provider != "local":
            raise ValueError("provider profile is not local")
        dependency_available = importlib.util.find_spec("sentence_transformers") is not None
        selection = self.secrets.registry.select(profile.name)
        loaded = bool(getattr(selection.embedding_provider, "loaded", False))
        details: dict[str, Any] = {
            "profile_name": profile.name,
            "dependency_available": dependency_available,
            "model_loaded": loaded,
            "download_attempted": False,
        }
        if allow_download and dependency_available:
            tested = await self._provider_profile(profile.name, "embedding")
            details.update(
                model_loaded=bool(getattr(selection.embedding_provider, "loaded", False)),
                download_attempted=True,
                model=selection.embedding_model,
                dimensions=tested["details"].get("dimensions"),
            )
            ok = bool(tested["ok"])
        else:
            ok = dependency_available
        return {
            "ok": ok,
            "result": "ready" if ok else "dependency_unavailable",
            "details": details,
            "warning": ("Model loading may download files when allow_download is true."),
        }
