from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Any, Literal, Never

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from pzp_ai_bot.ai.protocols import ChatMessage
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.embedding_control_errors import CONTROL_ERROR_STATUS, control_error_code
from pzp_ai_bot.memory.local_preparation import prepare_local_model
from pzp_ai_bot.memory.scope import Scope, parse_scope

from .schemas import ControlPlaneConflictEnvelope, ControlPlaneErrorEnvelope


class EmbeddingSpaceView(BaseModel):
    id: int
    key: str
    provider_profile: str
    model: str
    dimension: int
    created_at: datetime


class EmbeddingSpaceCoverage(EmbeddingSpaceView):
    eligible: int
    indexed: int
    missing: int
    stale: int


class EmbeddingJobView(BaseModel):
    id: int
    scope: str
    provider_profile: str
    model: str
    space_id: int | None
    status: Literal["queued", "running", "ready", "failed", "cancelled", "activated"]
    version: int
    processed: int
    total: int
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class EmbeddingIndexesView(BaseModel):
    scope: str
    active: EmbeddingSpaceView | None
    spaces: list[EmbeddingSpaceCoverage]
    jobs: list[EmbeddingJobView]
    legacy_unverified: int = 0


class EmbeddingPreviewView(BaseModel):
    scope: str
    provider_profile: str
    model: str
    eligible: int
    indexed: int
    missing: int
    cost_warning: str | None
    local_preparation_required: bool
    expected_batches: int = 0


class EmbeddingJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str = Field(default="all", min_length=1, max_length=128)
    provider_profile: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    model: str | None = Field(default=None, min_length=1, max_length=256)


class EmbeddingJobRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_batches: int = Field(default=1, strict=True, ge=1, le=10)


class EmbeddingJobVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(strict=True, ge=1)


class EmbeddingJobActivateRequest(EmbeddingJobVersionRequest):
    confirmation: str = Field(min_length=1, max_length=128)


class LocalModelPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["chat", "embedding"]
    allow_download: bool = Field(default=False, strict=True)


class LocalModelPrepareView(BaseModel):
    provider_profile: str
    mode: Literal["chat", "embedding"]
    loaded: bool
    downloaded: bool
    download_bytes: int
    resolved_revision: str | None = None


class ProviderProfileProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["chat", "embedding"]


class ProviderProfileProbeView(BaseModel):
    success: bool
    mode: Literal["chat", "embedding"]
    detail: str


def _scope(raw: str) -> Scope:
    try:
        return parse_scope(raw)
    except ValueError as exc:
        _raise_control_error(exc)


def _raise_control_error(exc: Exception) -> Never:
    from .app import ControlPlaneAPIError

    code = control_error_code(exc)
    status = CONTROL_ERROR_STATUS[code]
    if status == 404:
        raise ControlPlaneAPIError(404, code, "The embedding resource was not found.") from exc
    if status == 409:
        raise ControlPlaneAPIError(
            409, code, "The embedding index changed or is not ready for this action.", current={}
        ) from exc
    raise ControlPlaneAPIError(
        422, code, "The embedding or local preparation request could not be completed safely."
    ) from exc


async def _result(operation: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        return await operation
    except (ValueError, RuntimeError, TimeoutError) as exc:
        _raise_control_error(exc)


def build_embedding_router() -> APIRouter:
    from .app import _check_rate_limit, _require_bot_admin, _require_step_up

    router = APIRouter(prefix="/api/control/v1", tags=["embedding-indexes"])
    errors: dict[int | str, dict[str, Any]] = {
        status: {"model": ControlPlaneErrorEnvelope} for status in (401, 403, 404, 429)
    }
    errors[409] = {"model": ControlPlaneConflictEnvelope}
    errors[422] = {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"}
    owner = {"x-pzp-capability": "bot_admin"}

    def limit(request: Request, context: Any, action: str) -> None:
        _check_rate_limit(
            request,
            bucket=f"embedding_{action}",
            limit=context.settings.control_plane_provider_test_rate_limit,
        )

    @router.get(
        "/embedding-indexes",
        response_model=EmbeddingIndexesView,
        responses=errors,
        openapi_extra=owner,
    )
    async def status(
        request: Request, scope: str = Query(default="all", max_length=128)
    ) -> dict[str, Any]:
        context, _, _, _ = await _require_bot_admin(request)
        return await _result(context.memory.embedding_indexes.status(_scope(scope)))

    @router.get(
        "/embedding-indexes/preview",
        response_model=EmbeddingPreviewView,
        responses=errors,
        openapi_extra=owner,
    )
    async def preview(
        request: Request,
        provider_profile: str = Query(pattern=r"^[a-z0-9_-]{1,64}$"),
        scope: str = Query(default="all", max_length=128),
        model: str | None = Query(default=None, min_length=1, max_length=256),
    ) -> dict[str, Any]:
        context, _, _, _ = await _require_bot_admin(request)
        return await _result(
            context.memory.embedding_indexes.preview(_scope(scope), provider_profile, model=model)
        )

    @router.post(
        "/embedding-indexes/jobs",
        response_model=EmbeddingJobView,
        responses=errors,
        openapi_extra=owner,
    )
    async def start(request: Request, payload: EmbeddingJobCreateRequest) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        limit(request, context, "start")
        return await _result(
            context.memory.embedding_indexes.create_job(
                _scope(payload.scope),
                payload.provider_profile,
                model=payload.model,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        )

    @router.get(
        "/embedding-indexes/jobs/{job_id}",
        response_model=EmbeddingJobView,
        responses=errors,
        openapi_extra=owner,
    )
    async def job(request: Request, job_id: Annotated[int, Path(ge=1)]) -> dict[str, Any]:
        context, _, _, _ = await _require_bot_admin(request)
        return await _result(context.memory.embedding_indexes.get_job(job_id))

    @router.post(
        "/embedding-indexes/jobs/{job_id}/run",
        response_model=EmbeddingJobView,
        responses=errors,
        openapi_extra=owner,
    )
    async def run(
        request: Request, job_id: Annotated[int, Path(ge=1)], payload: EmbeddingJobRunRequest
    ) -> dict[str, Any]:
        context, _, _, _ = await _require_bot_admin(request)
        limit(request, context, "run")
        return await _result(
            context.memory.embedding_indexes.run_job(job_id, max_batches=payload.max_batches)
        )

    @router.post(
        "/embedding-indexes/jobs/{job_id}/resume",
        response_model=EmbeddingJobView,
        responses=errors,
        openapi_extra=owner,
    )
    async def resume(
        request: Request, job_id: Annotated[int, Path(ge=1)], payload: EmbeddingJobVersionRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        limit(request, context, "resume")
        return await _result(
            context.memory.embedding_indexes.resume_job(
                job_id,
                version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        )

    @router.post(
        "/embedding-indexes/jobs/{job_id}/cancel",
        response_model=EmbeddingJobView,
        responses=errors,
        openapi_extra=owner,
    )
    async def cancel(
        request: Request, job_id: Annotated[int, Path(ge=1)], payload: EmbeddingJobVersionRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        limit(request, context, "cancel")
        return await _result(
            context.memory.embedding_indexes.cancel_job(
                job_id,
                version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        )

    @router.post(
        "/embedding-indexes/jobs/{job_id}/activate",
        response_model=EmbeddingJobView,
        responses=errors,
        openapi_extra={
            "x-pzp-capability": "bot_admin_step_up",
            "x-pzp-step-up-action": "embedding:activate",
        },
    )
    async def activate(
        request: Request, job_id: Annotated[int, Path(ge=1)], payload: EmbeddingJobActivateRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        limit(request, context, "activate")
        _require_step_up(authenticated, action="embedding:activate", context=context)
        if payload.confirmation != f"ACTIVATE {job_id}":
            _raise_control_error(ValueError("embedding_confirmation_required"))
        return await _result(
            context.memory.embedding_indexes.activate_job(
                job_id,
                version=payload.version,
                confirmation=payload.confirmation,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        )

    @router.post(
        "/providers/{name}/prepare-local",
        response_model=LocalModelPrepareView,
        responses=errors,
        openapi_extra={**owner, "x-pzp-download-step-up-action": "local_model:download"},
    )
    async def prepare(
        request: Request, name: str, payload: LocalModelPrepareRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        limit(request, context, "prepare_local")
        if payload.allow_download:
            _require_step_up(authenticated, action="local_model:download", context=context)
        return await _result(
            prepare_local_model(
                context,
                name,
                mode=payload.mode,
                allow_download=payload.allow_download,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        )

    @router.post(
        "/providers/{name}/test",
        response_model=ProviderProfileProbeView,
        responses=errors,
        openapi_extra=owner,
    )
    async def probe(
        request: Request, name: str, payload: ProviderProfileProbeRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="provider_profile_test",
            limit=context.settings.control_plane_provider_test_rate_limit,
        )
        try:
            registry = context.memory.provider_registry
            if payload.mode not in registry.profile_capabilities(name):
                raise ValueError("embedding_profile_invalid")
            selected = registry.select(name)
        except ValueError as exc:
            _raise_control_error(exc)
        operations = context.memory._provider_operations
        operation = f"provider_profile_test_{payload.mode}"
        metadata = {"provider_profile": selected.profile.name, "test_mode": payload.mode}
        if payload.mode == "chat":
            execution = await operations.execute_chat(
                selected,
                [ChatMessage(role="user", content="PZP synthetic check. Reply OK.")],
                operation=operation,
                metadata=metadata,
                temperature=0,
                max_tokens=32,
                reasoning_effort=None,
            )
        else:
            execution = await operations.generate_embeddings(
                selected, ["PZP synthetic check"], operation=operation, metadata=metadata
            )
        success = execution.failure is None
        async with session_scope(context.session_factory) as session:
            await operations.add_usage(session, execution.telemetry)
            await EventRepository(session).add(
                event_type="provider.profile_tested",
                user_id=authenticated.identity.telegram_user_id,
                payload={**metadata, "success": success},
            )
        return {
            "success": success,
            "mode": payload.mode,
            "detail": "Provider check completed successfully."
            if success
            else "Provider check failed. Check configuration, credentials and local preparation.",
        }

    return router
