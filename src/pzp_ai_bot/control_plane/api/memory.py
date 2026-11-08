from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.transfer import MemoryExportFormat

from ..auth.sessions import AuthenticatedSession
from ..schemas import (
    ControlPlaneErrorEnvelope,
    MemoryItemPage,
    MemoryItemRefreshRequest,
    MemoryItemRefreshResponse,
    MemoryItemResponse,
    MemoryRawSourcesResponse,
    MemoryReviewMutationResponse,
    MemoryReviewPage,
)
from .common import (
    ControlPlaneAPIError,
    _as_utc,
    _authenticate,
    _require_memory_scope,
    _services,
    _session_token,
)


def _build_memory_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/memory", tags=["memory"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        404: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
        429: {"model": ControlPlaneErrorEnvelope},
    }

    @router.get(
        "/export",
        response_class=StreamingResponse,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def export_memory(
        request: Request,
        scope: str = Query(min_length=1, max_length=128),
        fmt: MemoryExportFormat = Query(default="md", alias="format"),
    ) -> StreamingResponse:
        context, parsed, _ = await _require_memory_scope(request, scope)
        suffix = "md" if fmt == "md" else "jsonl"
        media_type = "text/markdown" if fmt == "md" else "application/x-ndjson"
        return StreamingResponse(
            _memory_export_stream(context, None if parsed.is_global else parsed, fmt),
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="memory_{parsed.filename_stem}.{suffix}"'
                ),
                "Cache-Control": "no-store, private",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get(
        "/items",
        response_model=MemoryItemPage,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def list_memory_items(
        request: Request,
        scope: str = Query(min_length=1, max_length=128),
        kind: str | None = Query(default=None, min_length=1, max_length=64),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        context, parsed, _ = await _require_memory_scope(request, scope)
        items = await context.memory.browse_items_page(
            None if parsed.is_global else parsed, kind=kind, after_id=cursor, limit=limit + 1
        )
        has_more = len(items) > limit
        page = items[:limit]
        return {
            "items": [_memory_item_payload(item) for item in page],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }

    @router.get(
        "/items/{item_id}",
        response_model=MemoryItemResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def get_memory_item(request: Request, item_id: int) -> dict[str, Any]:
        _context, item = await _authorized_memory_item(request, item_id)
        return _memory_item_payload(item)

    @router.post(
        "/items/{item_id}/sources",
        response_model=MemoryRawSourcesResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def get_memory_item_sources(request: Request, item_id: int) -> dict[str, Any]:
        context, item = await _authorized_memory_item(request, item_id)
        authenticated = await _authenticate(
            request.app.state.control_plane_runtime, _session_token(request)
        )
        scope, sources = await context.memory.memory_item_sources(
            item.id, accessed_by=authenticated.identity.telegram_user_id, limit=100
        )
        return {
            "item_id": item.id,
            "scope": scope.key,
            "sources": [
                {
                    "telegram_message_id": source.telegram_message_id,
                    "user_id": source.user_id,
                    "username": source.username,
                    "text": source.text,
                    "created_at": _as_utc(source.created_at),
                    "message_permalink": source.message_permalink,
                }
                for source in sources
            ],
        }

    @router.post(
        "/items/{item_id}/refresh",
        response_model=MemoryItemRefreshResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def refresh_memory_item(
        request: Request, item_id: int, payload: MemoryItemRefreshRequest
    ) -> dict[str, Any]:
        context, item = await _authorized_memory_item(request, item_id)
        authenticated = await _authenticate(
            request.app.state.control_plane_runtime, _session_token(request)
        )
        try:
            result = await context.memory.refresh_memory_item(
                item.scope,
                item.id,
                mode=payload.mode,
                updated_by=authenticated.identity.telegram_user_id,
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "memory_refresh_invalid", "The memory refresh request is invalid."
            ) from exc
        return {
            "id": result.id,
            "scope": result.scope.key,
            "kind": result.kind,
            "mode": result.mode,
            "summarized": result.summarized,
            "embeddings_refreshed": result.embeddings_refreshed,
        }

    @router.delete(
        "/items/{item_id}",
        response_model=MemoryItemResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def delete_memory_item(request: Request, item_id: int) -> dict[str, Any]:
        context, item = await _authorized_memory_item(request, item_id)
        authenticated = await _authenticate(
            request.app.state.control_plane_runtime, _session_token(request)
        )
        deleted = await context.memory.delete_memory_item(
            item.scope, item.id, deleted_by=authenticated.identity.telegram_user_id
        )
        return _memory_item_payload(deleted)

    @router.get(
        "/reviews",
        response_model=MemoryReviewPage,
        openapi_extra={"x-pzp-capability": "scope_admin"},
        responses=common_errors,
    )
    async def list_memory_reviews(
        request: Request,
        scope: str = Query(min_length=1, max_length=128),
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        context, parsed, _ = await _require_memory_scope(request, scope)
        reviews = await context.memory.pending_reviews_page(
            None if parsed.is_global else parsed, after_id=cursor, limit=limit + 1
        )
        has_more = len(reviews) > limit
        page = reviews[:limit]
        return {
            "items": [_memory_review_payload(review) for review in page],
            "next_cursor": page[-1].id if has_more and page else None,
            "has_more": has_more,
        }

    @router.post(
        "/reviews/{review_id}/approve",
        response_model=MemoryReviewMutationResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def approve_memory_review(request: Request, review_id: int) -> dict[str, Any]:
        context, authenticated = await _authorized_pending_review(request, review_id)
        try:
            item_id = await context.memory.approve_review(
                review_id, reviewed_by=authenticated.identity.telegram_user_id
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                404, "memory_review_not_found", "The pending memory review was not found."
            ) from exc
        return {"review_id": review_id, "status": "approved", "knowledge_item_id": item_id}

    @router.post(
        "/reviews/{review_id}/reject",
        response_model=MemoryReviewMutationResponse,
        openapi_extra={"x-pzp-capability": "persisted_scope_admin"},
        responses=common_errors,
    )
    async def reject_memory_review(request: Request, review_id: int) -> dict[str, Any]:
        context, authenticated = await _authorized_pending_review(request, review_id)
        try:
            await context.memory.reject_review(
                review_id, reviewed_by=authenticated.identity.telegram_user_id
            )
        except ValueError as exc:
            raise ControlPlaneAPIError(
                404, "memory_review_not_found", "The pending memory review was not found."
            ) from exc
        return {"review_id": review_id, "status": "rejected", "knowledge_item_id": None}

    return router


async def _authorized_memory_item(request: Request, item_id: int) -> tuple[AppContext, Any]:
    context, _ = _services(request)
    try:
        item = await context.memory.memory_item(item_id)
    except ValueError as exc:
        raise ControlPlaneAPIError(
            404, "memory_item_not_found", "The requested memory item was not found."
        ) from exc
    await _require_memory_scope(request, item.scope.key)
    return context, item


async def _authorized_pending_review(
    request: Request, review_id: int
) -> tuple[AppContext, AuthenticatedSession]:
    context, _ = _services(request)
    try:
        scope = await context.memory.pending_review_scope(review_id)
    except ValueError as exc:
        raise ControlPlaneAPIError(
            404, "memory_review_not_found", "The pending memory review was not found."
        ) from exc
    context, _, authenticated = await _require_memory_scope(request, scope.key)
    return context, authenticated


def _memory_item_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "scope": item.scope.key,
        "kind": item.kind,
        "content": item.content,
        "source_message_ids": item.source_message_ids,
        "metadata": item.metadata,
        "created_at": _as_utc(item.created_at),
        "updated_at": _as_utc(item.updated_at),
    }


def _memory_review_payload(review: Any) -> dict[str, Any]:
    return {
        "id": review.id,
        "scope": review.scope.key,
        "kind": review.kind,
        "content": review.content,
        "source_message_ids": review.source_message_ids,
        "created_at": _as_utc(review.created_at),
    }


async def _memory_export_stream(
    context: AppContext, scope: Scope | None, fmt: MemoryExportFormat
) -> AsyncGenerator[bytes, None]:
    chunks = context.memory.export_memory_chunks(scope, fmt=fmt)
    try:
        async for chunk in chunks:
            yield chunk.text.encode("utf-8")
    finally:
        close = getattr(chunks, "aclose", None)
        if close is not None:
            await close()
