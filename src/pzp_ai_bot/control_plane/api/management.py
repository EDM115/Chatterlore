from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.memory.embedding_settings import EmbeddingMigrationRequiredError
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.settings_profiles import SettingsVersionConflictError

from ..authorization import CAPABILITY_CHAT_ADMIN
from ..management import ControlPlaneManagementError
from ..runtime import ControlPlaneRuntime
from ..schemas import (
    BotAdminCreateRequest,
    BotAdminResponse,
    ChatAdminCreateRequest,
    ChatAdminResponse,
    ChatEnrollmentCreateRequest,
    ChatEnrollmentResponse,
    ChatForgetResponse,
    ConfirmedChatActionRequest,
    ControlPlaneConflictEnvelope,
    ControlPlaneErrorEnvelope,
    ManagedChatMutationResponse,
    ManagedChatPage,
    ManagedChatResponse,
    ManagedChatStatusRequest,
    RuntimeSettingResponse,
    RuntimeSettingsMutationResponse,
    RuntimeSettingsUpdateRequest,
    ScopeSettingsMutationResponse,
    ScopeSettingsResponse,
    ScopeSettingsUpdateRequest,
    VersionedMutationRequest,
)
from .common import (
    ControlPlaneAPIError,
    _raise_management_error,
    _raise_settings_conflict,
    _require_authenticated,
    _require_bot_admin,
    _require_chat_admin,
    _require_step_up,
    _telegram_bot,
)


def _build_management_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1", tags=["management"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        404: {"model": ControlPlaneErrorEnvelope},
        409: {"model": ControlPlaneConflictEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
    }

    @router.get(
        "/bot-settings/admins",
        response_model=list[BotAdminResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_bot_admins(request: Request) -> list[Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return await runtime.management.list_bot_admins()

    @router.post(
        "/bot-settings/admins",
        response_model=BotAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_bot_admin(request: Request, payload: BotAdminCreateRequest) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.management.grant_bot_admin(
                telegram_user_id=payload.telegram_user_id,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.delete(
        "/bot-settings/admins/{telegram_user_id}",
        response_model=BotAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses=common_errors,
    )
    async def delete_bot_admin(
        request: Request, telegram_user_id: int, payload: VersionedMutationRequest = Body()
    ) -> Any:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _require_step_up(authenticated, action="bot_admin:delete", context=context)
        try:
            return await runtime.management.revoke_bot_admin(
                telegram_user_id=telegram_user_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/bot-settings/runtime",
        response_model=list[RuntimeSettingResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_runtime_settings(request: Request) -> list[Any]:
        context, _, _, _ = await _require_bot_admin(request)
        return await context.memory.list_runtime_settings()

    @router.patch(
        "/bot-settings/runtime",
        response_model=RuntimeSettingsMutationResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def update_runtime_settings(
        request: Request, payload: RuntimeSettingsUpdateRequest
    ) -> dict[str, Any]:
        context, _, authenticated, _ = await _require_bot_admin(request)
        try:
            mutation = await context.memory.set_runtime_setting_versioned(
                payload.updates,
                expected_version=payload.version,
                updated_by=authenticated.identity.telegram_user_id,
            )
        except SettingsVersionConflictError as exc:
            _raise_settings_conflict(exc)
        except EmbeddingMigrationRequiredError as exc:
            raise ControlPlaneAPIError(
                409,
                "embedding_migration_required",
                "Rebuild and activate the embedding index before changing its route.",
            ) from exc
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "runtime_settings_invalid", "The runtime settings update is invalid."
            ) from exc
        return {
            "items": await context.memory.list_runtime_settings(),
            "version": mutation.version,
            "audit_event_id": mutation.audit_event_id,
        }

    @router.post(
        "/chats/enrollment-requests",
        response_model=ChatEnrollmentResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_chat_enrollment(request: Request, payload: ChatEnrollmentCreateRequest) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        bot = _telegram_bot(request)
        try:
            return await runtime.management.create_chat_enrollment(
                telegram_user_id=authenticated.identity.telegram_user_id,
                bot=bot,
                delivery=payload.delivery,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/chats/enrollment-requests/{request_id}",
        response_model=ChatEnrollmentResponse,
        openapi_extra={"x-pzp-capability": "authenticated"},
        responses=common_errors,
    )
    async def get_chat_enrollment(request: Request, request_id: int) -> Any:
        _, runtime, authenticated, _ = await _require_authenticated(request)
        try:
            return await runtime.management.get_chat_enrollment(
                request_id=request_id, telegram_user_id=authenticated.identity.telegram_user_id
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/chats",
        response_model=ManagedChatPage,
        openapi_extra={"x-pzp-capability": "authenticated_admin"},
        responses=common_errors,
    )
    async def list_managed_chats(
        request: Request,
        cursor: int | None = Query(default=None, ge=1),
        limit: int = Query(default=100, ge=1, le=100),
    ) -> dict[str, Any]:
        _, runtime, authenticated, actor = await _require_authenticated(request)
        chats, next_cursor, has_more = await runtime.management.list_managed_chats_page(
            limit=limit, before_id=cursor
        )
        if actor.is_bot_admin:
            return {"items": chats, "next_cursor": next_cursor, "has_more": has_more}
        bot = getattr(request.app.state, "telegram_bot", None)
        visible: list[Any] = []
        for chat in chats:
            if chat.status != "active":
                continue
            resolved = await runtime.authorization.resolve(
                authenticated.identity.telegram_user_id, bot=bot, chat_id=chat.chat_id
            )
            if CAPABILITY_CHAT_ADMIN in resolved.capabilities_for_chat(chat.chat_id):
                visible.append(chat)
        return {"items": visible, "next_cursor": next_cursor, "has_more": has_more}

    @router.patch(
        "/chats/{chat_id}/status",
        response_model=ManagedChatMutationResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def update_managed_chat_status(
        request: Request, chat_id: int, payload: ManagedChatStatusRequest
    ) -> Any:
        _, runtime, authenticated, _ = await _require_chat_admin(request, chat_id)
        try:
            managed = await runtime.management.set_chat_status(
                chat_id=chat_id,
                status=payload.status,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
            return _managed_chat_mutation_payload(managed)
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.post(
        "/chats/{chat_id}/leave",
        response_model=ManagedChatResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses=common_errors,
    )
    async def leave_managed_chat(
        request: Request, chat_id: int, payload: ConfirmedChatActionRequest
    ) -> Any:
        context, runtime, authenticated, actor = await _require_chat_admin(request, chat_id)
        if not actor.is_bot_admin:
            raise ControlPlaneAPIError(
                403, "bot_admin_required", "Bot administrator access is required."
            )
        _require_step_up(authenticated, action="chat:leave", context=context)
        if payload.confirmation != f"LEAVE {chat_id}":
            raise ControlPlaneAPIError(
                422, "chat_confirmation_invalid", f"Type LEAVE {chat_id} to confirm this action."
            )
        try:
            return await runtime.management.leave_chat(
                chat_id=chat_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
                bot=_telegram_bot(request),
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.post(
        "/chats/{chat_id}/forget",
        response_model=ChatForgetResponse,
        openapi_extra={"x-pzp-capability": "bot_admin_step_up"},
        responses=common_errors,
    )
    async def forget_managed_chat(
        request: Request, chat_id: int, payload: ConfirmedChatActionRequest
    ) -> dict[str, Any]:
        context, runtime, authenticated, actor = await _require_chat_admin(request, chat_id)
        if not actor.is_bot_admin:
            raise ControlPlaneAPIError(
                403, "bot_admin_required", "Bot administrator access is required."
            )
        _require_step_up(authenticated, action="chat:forget", context=context)
        if payload.confirmation != f"FORGET {chat_id}":
            raise ControlPlaneAPIError(
                422, "chat_confirmation_invalid", f"Type FORGET {chat_id} to confirm this action."
            )
        managed = await runtime.management.get_managed_chat(chat_id)
        if managed.version != payload.version:
            raise ControlPlaneAPIError(
                409,
                "version_conflict",
                "The resource changed concurrently.",
                current={"version": managed.version},
            )
        result = await runtime.management.forget_chat(
            chat_id=chat_id, actor_user_id=authenticated.identity.telegram_user_id
        )
        return {"chat_id": chat_id, "version": managed.version, **result}

    @router.get(
        "/chats/{chat_id}/admins",
        response_model=list[ChatAdminResponse],
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_chat_admins(request: Request, chat_id: int) -> list[Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return await runtime.management.list_chat_admins(chat_id)

    @router.post(
        "/chats/{chat_id}/admins",
        response_model=ChatAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_chat_admin(
        request: Request, chat_id: int, payload: ChatAdminCreateRequest
    ) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.management.grant_chat_admin(
                chat_id=chat_id,
                telegram_user_id=payload.telegram_user_id,
                actor_user_id=authenticated.identity.telegram_user_id,
                bot=_telegram_bot(request),
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.delete(
        "/chats/{chat_id}/admins/{telegram_user_id}",
        response_model=ChatAdminResponse,
        openapi_extra={"x-pzp-capability": "bot_admin"},
        responses=common_errors,
    )
    async def delete_chat_admin(
        request: Request,
        chat_id: int,
        telegram_user_id: int,
        payload: VersionedMutationRequest = Body(),
    ) -> Any:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            return await runtime.management.revoke_chat_admin(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except ControlPlaneManagementError as exc:
            _raise_management_error(exc)

    @router.get(
        "/chats/{chat_id}/settings",
        response_model=ScopeSettingsResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def get_chat_settings(
        request: Request, chat_id: int, thread_id: int | None = Query(default=None)
    ) -> dict[str, Any]:
        context, runtime, _, _ = await _require_chat_admin(request, chat_id)
        return await _scope_settings_payload(context, runtime, Scope(chat_id, thread_id))

    @router.patch(
        "/chats/{chat_id}/settings",
        response_model=ScopeSettingsMutationResponse,
        openapi_extra={"x-pzp-capability": "chat_admin"},
        responses=common_errors,
    )
    async def update_chat_settings(
        request: Request,
        chat_id: int,
        payload: ScopeSettingsUpdateRequest,
        thread_id: int | None = Query(default=None),
    ) -> dict[str, Any]:
        context, runtime, authenticated, _ = await _require_chat_admin(request, chat_id)
        if (
            not payload.settings
            and payload.learning_enabled is None
            and payload.allow_admins is None
        ):
            raise ControlPlaneAPIError(
                422, "scope_settings_empty", "At least one scope setting or policy is required."
            )
        scope = Scope(chat_id, thread_id)
        try:
            mutation = await context.memory.update_scope_configuration(
                scope,
                payload.settings,
                learning_enabled=payload.learning_enabled,
                allow_admins=payload.allow_admins,
                expected_version=payload.version,
                updated_by=authenticated.identity.telegram_user_id,
            )
        except SettingsVersionConflictError as exc:
            _raise_settings_conflict(exc)
        except EmbeddingMigrationRequiredError as exc:
            raise ControlPlaneAPIError(
                409,
                "embedding_migration_required",
                "Rebuild and activate the embedding index before changing its route.",
            ) from exc
        except ValueError as exc:
            raise ControlPlaneAPIError(
                422, "scope_settings_invalid", "The scope settings update is invalid."
            ) from exc
        response = await _scope_settings_payload(context, runtime, scope)
        response["audit_event_id"] = mutation.audit_event_id
        return response

    return router


async def _scope_settings_payload(
    context: AppContext, runtime: ControlPlaneRuntime, scope: Scope
) -> dict[str, Any]:
    settings = await runtime.management.scope_settings(scope)
    policy = await context.memory.learn_status(scope)
    return {
        "scope": settings.scope,
        "effective": settings.effective,
        "inherited": settings.inherited,
        "overrides": settings.overrides,
        "policy": {
            "learning_enabled": bool(policy["learning_enabled"]),
            "allow_admins": bool(policy["allow_admins"]),
        },
        "version": settings.version,
    }


def _managed_chat_mutation_payload(managed: Any) -> dict[str, Any]:
    if managed.audit_event_id is None:
        raise RuntimeError("managed chat mutation did not produce an audit event")
    return {
        "chat_id": managed.chat_id,
        "title": managed.title,
        "username": managed.username,
        "status": managed.status,
        "version": managed.version,
        "updated_at": managed.updated_at,
        "audit_event_id": managed.audit_event_id,
    }
