from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from chatterlore.ai.local_runtime import (
    LOCAL_CONFIG_FILES,
    LOCAL_TOKENIZER_FILES,
    import_local_dependency,
    local_directory_revision,
)
from chatterlore.control_plane.secrets.service import ProviderSecretService
from chatterlore.db.repos.events import EventRepository
from chatterlore.db.session import session_scope
from chatterlore.local_models import LocalModelOptions

if TYPE_CHECKING:
    from chatterlore.app_context import AppContext


@dataclass(frozen=True)
class _SnapshotPlan:
    revision: str
    filenames: list[str]
    total_bytes: int


def _snapshot_plan(
    hub: Any, model: str, revision: str | None, *, tokenizer_only: bool
) -> _SnapshotPlan:
    try:
        metadata = hub.HfApi().model_info(
            repo_id=model, revision=revision, files_metadata=True, timeout=15
        )
    except Exception:
        raise ValueError("local_download_metadata_unavailable") from None
    if not isinstance(getattr(metadata, "sha", None), str) or not re.fullmatch(
        r"[a-fA-F0-9]{40}", metadata.sha
    ):
        raise ValueError("local_download_metadata_unavailable")
    filenames: list[str] = []
    total = 0
    for item in getattr(metadata, "siblings", None) or []:
        name = getattr(item, "rfilename", None)
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_./-]+", name):
            raise ValueError("local_download_metadata_unavailable")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ValueError("local_download_metadata_unavailable")
        allowed = path.name in LOCAL_TOKENIZER_FILES or (
            not tokenizer_only
            and (
                path.name in LOCAL_CONFIG_FILES
                or path.suffix == ".safetensors"
                or path.name.endswith(".safetensors.index.json")
            )
        )
        if not allowed:
            continue
        size = getattr(item, "size", None)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("local_download_metadata_unavailable")
        total += size
        filenames.append(name)
    if not filenames or (
        not tokenizer_only and not any(name.endswith(".safetensors") for name in filenames)
    ):
        raise ValueError("local_download_metadata_unavailable")
    return _SnapshotPlan(metadata.sha, sorted(filenames), total)


def _fetch_snapshot(hub: Any, model: str, cache_dir: str, plan: _SnapshotPlan) -> str:
    try:
        snapshot = hub.snapshot_download(
            repo_id=model,
            revision=plan.revision,
            cache_dir=cache_dir,
            allow_patterns=plan.filenames,
            max_workers=2,
            local_files_only=False,
        )
        path = Path(snapshot).resolve()
        if not path.is_relative_to(Path(cache_dir).resolve()):
            raise ValueError("invalid cache result")
        return str(path)
    except Exception:
        raise ValueError("local_preparation_failed") from None


def _cached_snapshot(hub: Any, model: str, revision: str | None, cache_dir: str) -> tuple[str, str]:
    try:
        path = Path(
            hub.snapshot_download(
                repo_id=model, revision=revision, cache_dir=cache_dir, local_files_only=True
            )
        ).resolve()
        if not path.is_relative_to(Path(cache_dir).resolve()) or not re.fullmatch(
            r"[a-fA-F0-9]{40}", path.name
        ):
            raise ValueError("invalid cache snapshot")
        return str(path), path.name.lower()
    except Exception:
        raise ValueError("local_preparation_required") from None


async def _pin_profile(
    service: ProviderSecretService,
    baseline: Any,
    mode: str,
    raw_options: dict[str, Any],
    revision: str,
    tokenizer_revision: str,
    actor_user_id: int | None,
) -> None:
    field = f"local_{mode}_options"
    pinned_options = {**raw_options, "revision": revision}
    if raw_options.get("tokenizer_revision") is not None or tokenizer_revision != revision:
        pinned_options["tokenizer_revision"] = tokenizer_revision
    if pinned_options == raw_options:
        return
    if baseline.source == "managed":
        await service.update_profile(
            baseline.name,
            expected_version=baseline.version,
            updates={field: pinned_options},
            actor_user_id=actor_user_id or 0,
            now=datetime.now(UTC),
        )
    else:
        values = baseline.as_dict()
        values[field] = pinned_options
        values["redaction_patterns"] = list(values["redaction_patterns"])
        await service.create_profile(
            values, actor_user_id=actor_user_id or 0, now=datetime.now(UTC)
        )


async def prepare_local_model(
    context: AppContext,
    profile_name: str,
    *,
    mode: Literal["chat", "embedding"],
    allow_download: bool = False,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    registry = context.memory.provider_registry
    profile = registry.profile_by_name(profile_name)
    if (
        profile.provider != "local"
        or mode not in {"chat", "embedding"}
        or mode not in registry.profile_capabilities(profile_name)
    ):
        raise ValueError("local_preparation_unsupported")
    raw_options = profile.local_chat_options if mode == "chat" else profile.local_embedding_options
    options = LocalModelOptions.model_validate(context.settings.local_runtime_options(raw_options))
    model = profile.chat_model if mode == "chat" else profile.embedding_model
    context.settings.validate_local_paths(model, options.model_dump())
    service = (
        context.control_plane.secrets
        if context.control_plane
        else ProviderSecretService(context.settings, context.session_factory, registry)
    )
    baseline = await service.get_profile(profile.name)
    if (
        getattr(baseline, f"{mode}_model") != model
        or getattr(baseline, f"local_{mode}_options") != raw_options
    ):
        raise ValueError("local_preparation_profile_changed")
    audit = {
        "provider_profile": profile.name,
        "mode": mode,
        "actor_kind": "telegram_user" if actor_user_id is not None else "local_operator",
    }
    async with session_scope(context.session_factory) as session:
        await EventRepository(session).add(
            event_type="local_model.preparation_requested",
            user_id=actor_user_id,
            payload={**audit, "download_requested": allow_download},
        )
    try:
        paths: dict[str, str] = {}
        download_bytes = 0
        downloaded = False
        local_directory = await asyncio.to_thread(Path(model).is_dir)
        cache_dir = options.cache_dir or str(context.settings.local_default_cache_dir)
        if local_directory:
            revision = await asyncio.to_thread(local_directory_revision, model)
            tokenizer_revision = revision
            paths = {"model_path": model, "tokenizer_path": model}
        else:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)?", model):
                raise ValueError("local_preparation_unsupported")
            if allow_download and options.local_files_only:
                raise ValueError("local_download_forbidden")
            hub = import_local_dependency("huggingface_hub")
            if allow_download:
                model_plan = await asyncio.to_thread(
                    _snapshot_plan, hub, model, options.revision, tokenizer_only=False
                )
                tokenizer_plan = model_plan
                if options.tokenizer_revision and options.tokenizer_revision != options.revision:
                    tokenizer_plan = await asyncio.to_thread(
                        _snapshot_plan, hub, model, options.tokenizer_revision, tokenizer_only=True
                    )
                download_bytes = model_plan.total_bytes + (
                    tokenizer_plan.total_bytes if tokenizer_plan is not model_plan else 0
                )
                if download_bytes > context.settings.local_download_max_bytes:
                    raise ValueError("local_download_too_large")
                revision, tokenizer_revision = model_plan.revision, tokenizer_plan.revision
                paths["model_path"] = await asyncio.to_thread(
                    _fetch_snapshot, hub, model, cache_dir, model_plan
                )
                paths["tokenizer_path"] = (
                    paths["model_path"]
                    if tokenizer_plan is model_plan
                    else await asyncio.to_thread(
                        _fetch_snapshot, hub, model, cache_dir, tokenizer_plan
                    )
                )
                downloaded = True
            else:
                paths["model_path"], revision = await asyncio.to_thread(
                    _cached_snapshot, hub, model, options.revision, cache_dir
                )
                paths["tokenizer_path"], tokenizer_revision = paths["model_path"], revision
                if options.tokenizer_revision and options.tokenizer_revision != options.revision:
                    paths["tokenizer_path"], tokenizer_revision = await asyncio.to_thread(
                        _cached_snapshot, hub, model, options.tokenizer_revision, cache_dir
                    )
        await _pin_profile(
            service, baseline, mode, raw_options, revision, tokenizer_revision, actor_user_id
        )
        await service.refresh_registry()
        current = registry.profile_by_name(profile_name)
        current_options = (
            current.local_chat_options if mode == "chat" else current.local_embedding_options
        )
        if (
            current.chat_model if mode == "chat" else current.embedding_model
        ) != model or current_options.get("revision") != revision:
            raise ValueError("local_preparation_profile_changed")
        selected = registry.select(profile_name)
        provider = selected.chat_provider if mode == "chat" else selected.embedding_provider
        prepare = getattr(provider, "prepare", None)
        if prepare is None:
            raise ValueError("local_preparation_unsupported")
        try:
            await prepare(allow_download=False, **paths)
        except (ValueError, RuntimeError, TimeoutError):
            raise ValueError("local_preparation_failed") from None
    except (ValueError, RuntimeError, TimeoutError):
        async with session_scope(context.session_factory) as session:
            await EventRepository(session).add(
                event_type="local_model.preparation_failed", user_id=actor_user_id, payload=audit
            )
        raise
    async with session_scope(context.session_factory) as session:
        await EventRepository(session).add(
            event_type="local_model.prepared",
            user_id=actor_user_id,
            payload={
                **audit,
                "download_bytes": download_bytes,
                "resolved_revision": revision,
                "tokenizer_revision": tokenizer_revision,
            },
        )
    return {
        "provider_profile": profile.name,
        "mode": mode,
        "loaded": bool(getattr(provider, "loaded", False)),
        "downloaded": downloaded,
        "download_bytes": download_bytes,
        "resolved_revision": revision,
    }
