from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from pzp_ai_bot.memory.contracts import AIRequestOptions
from pzp_ai_bot.memory.retrieval_service import EmbeddingCoverageRequiredError

from ..schemas import (
    ControlPlaneErrorEnvelope,
    QueryAskRequest,
    QueryAskResponse,
    QueryCatalogResponse,
    QuerySearchRequest,
    QuerySearchResponse,
)
from .common import ControlPlaneAPIError, _require_active_query_scope


def _build_query_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/query", tags=["query"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        404: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
        429: {"model": ControlPlaneErrorEnvelope},
    }

    @router.get(
        "/catalog",
        response_model=QueryCatalogResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def query_catalog(request: Request, scope: str = Query(min_length=1, max_length=128)):
        _, runtime, parsed = await _require_active_query_scope(request, scope)
        return await runtime.catalog.for_scope(parsed.key)

    @router.post(
        "/search",
        response_model=QuerySearchResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def query_search(request: Request, payload: QuerySearchRequest) -> dict[str, Any]:
        context, _, scope = await _require_active_query_scope(request, payload.scope)
        try:
            execution = await context.memory.search_with_metadata(
                scope,
                payload.query,
                top_k=payload.top_k,
                profile_name=payload.memory_profile,
                ai_options=_query_ai_options(payload),
            )
        except EmbeddingCoverageRequiredError as exc:
            raise ControlPlaneAPIError(
                409,
                "embedding_migration_required",
                "The selected embedding index is incomplete. "
                "Ask a bot administrator to rebuild it.",
            ) from exc
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "query_selection_invalid", "The query selection is invalid."
            ) from exc
        return _query_execution_payload(execution)

    @router.post(
        "/ask",
        response_model=QueryAskResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def query_ask(request: Request, payload: QueryAskRequest) -> dict[str, Any]:
        context, _, scope = await _require_active_query_scope(request, payload.scope)
        try:
            execution = await context.memory.ask_with_metadata(
                scope,
                payload.question,
                top_k=payload.top_k,
                profile_name=payload.memory_profile,
                ai_options=_query_ai_options(payload),
            )
        except EmbeddingCoverageRequiredError as exc:
            raise ControlPlaneAPIError(
                409,
                "embedding_migration_required",
                "The selected embedding index is incomplete. "
                "Ask a bot administrator to rebuild it.",
            ) from exc
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "query_selection_invalid", "The query selection is invalid."
            ) from exc
        return {
            **_query_execution_payload(execution),
            "answer": execution.answer,
            "insufficient_context": execution.insufficient_context,
        }

    return router


def _query_ai_options(payload: Any) -> AIRequestOptions:
    return AIRequestOptions(
        provider_profile=payload.provider_profile,
        retrieval_provider_profile=payload.retrieval_provider_profile,
        chat_provider_profile=payload.chat_provider_profile,
        chat_model=payload.chat_model,
        embedding_model=payload.embedding_model,
        reasoning_level=payload.reasoning_level,
    )


def _query_execution_payload(execution: Any) -> dict[str, Any]:
    selection = execution.selection
    return {
        "destination_scope": execution.destination_scope.key,
        "source_scopes": [scope.key for scope in execution.source_scopes],
        "memory_profile": execution.memory_profile,
        "selection": {
            "retrieval_provider_profile": selection.retrieval_provider_profile,
            "embedding_model": selection.embedding_model,
            "chat_provider_profile": selection.chat_provider_profile,
            "chat_model": selection.chat_model,
            "reasoning_level": selection.reasoning_level,
        },
        "results": [
            {
                "content": result.content[:4_000],
                "score": result.score,
                "source": result.source,
                "scope": result.scope.key,
                "source_message_ids": result.source_message_ids[:100],
            }
            for result in execution.results
        ],
        "elapsed_ms": execution.elapsed_ms,
    }
