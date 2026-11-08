from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from chatterlore.ai.credential_pool import ProviderCallError

from ..schemas import (
    ControlPlaneConflictEnvelope,
    ControlPlaneErrorEnvelope,
    DeleteMutationResponse,
    ProviderCredentialCreateRequest,
    ProviderCredentialDeleteRequest,
    ProviderCredentialMutationResponse,
    ProviderCredentialReplaceRequest,
    ProviderCredentialRevealResponse,
    ProviderCredentialSummary,
    ProviderCredentialTestRequest,
    ProviderCredentialTestResponse,
    ProviderCredentialUpdateRequest,
    ProviderPoolSummary,
    ProviderProfileCreateRequest,
    ProviderProfileDeleteRequest,
    ProviderProfileMutationResponse,
    ProviderProfileResponse,
    ProviderProfileUpdateRequest,
)
from ..secrets.service import ProviderSecretConflictError
from .common import (
    ControlPlaneAPIError,
    _check_rate_limit,
    _raise_provider_error,
    _require_bot_admin,
    _require_step_up,
)


def _build_provider_router() -> APIRouter:
    router = APIRouter(prefix="/api/control/v1/providers", tags=["providers"])
    common_errors: dict[int | str, dict[str, Any]] = {
        401: {"model": ControlPlaneErrorEnvelope},
        403: {"model": ControlPlaneErrorEnvelope},
        422: {"model": ControlPlaneErrorEnvelope, "description": "Unprocessable Content"},
    }
    versioned_errors: dict[int | str, dict[str, Any]] = {
        **common_errors,
        409: {"model": ControlPlaneConflictEnvelope},
    }

    @router.get(
        "",
        response_model=list[ProviderProfileResponse],
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_providers(request: Request) -> list[dict[str, Any]]:
        _, runtime, _, _ = await _require_bot_admin(request)
        return [profile.as_dict() for profile in await runtime.secrets.list_profiles()]

    @router.post(
        "",
        response_model=ProviderProfileMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=common_errors,
    )
    async def create_provider(
        request: Request, payload: ProviderProfileCreateRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            profile = await runtime.secrets.create_profile(
                payload.model_dump(),
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except ValueError as exc:
            _raise_provider_error(exc)
        return _profile_mutation_payload(profile)

    @router.get(
        "/{name}",
        response_model=ProviderProfileResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=common_errors,
    )
    async def get_provider(request: Request, name: str) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        try:
            return (await runtime.secrets.get_profile(name)).as_dict()
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.patch(
        "/{name}",
        response_model=ProviderProfileMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=versioned_errors,
    )
    async def update_provider(
        request: Request, name: str, payload: ProviderProfileUpdateRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        updates = payload.model_dump(exclude={"version"}, exclude_none=True)
        try:
            profile = await runtime.secrets.update_profile(
                name,
                expected_version=payload.version,
                updates=updates,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return _profile_mutation_payload(profile)

    @router.delete(
        "/{name}",
        response_model=DeleteMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin_step_up"},
        responses={**versioned_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def delete_provider(
        request: Request, name: str, payload: ProviderProfileDeleteRequest = Body()
    ) -> DeleteMutationResponse:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="provider_delete",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="profile:delete", context=context)
        try:
            audit_event_id = await runtime.secrets.delete_profile(
                name,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return DeleteMutationResponse(audit_event_id=audit_event_id, version=payload.version + 1)

    @router.get(
        "/{name}/credentials",
        response_model=list[ProviderCredentialSummary],
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=common_errors,
    )
    async def list_provider_credentials(request: Request, name: str) -> list[dict[str, Any]]:
        _, runtime, _, _ = await _require_bot_admin(request)
        try:
            return [item.as_dict() for item in await runtime.secrets.list_credentials(name)]
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.get(
        "/{name}/pool",
        response_model=ProviderPoolSummary,
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=common_errors,
    )
    async def get_provider_pool(request: Request, name: str) -> dict[str, Any]:
        _, runtime, _, _ = await _require_bot_admin(request)
        try:
            return (await runtime.credential_pool.summary(name)).as_dict()
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.post(
        "/{name}/credentials",
        response_model=ProviderCredentialMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin_step_up"},
        responses={**common_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def create_provider_credential(
        request: Request, name: str, payload: ProviderCredentialCreateRequest
    ) -> dict[str, Any]:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_create",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:create", context=context)
        if len(payload.value) > context.settings.control_plane_max_secret_length:
            raise ControlPlaneAPIError(
                422, "credential_invalid", "The provider credential is invalid."
            )
        try:
            credential = await runtime.secrets.create_credential(
                name,
                label=payload.label,
                value=payload.value,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except ValueError as exc:
            _raise_provider_error(exc)
        return _credential_mutation_payload(credential)

    @router.patch(
        "/{name}/credentials/{credential_id}",
        response_model=ProviderCredentialMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses=versioned_errors,
    )
    async def update_provider_credential(
        request: Request, name: str, credential_id: int, payload: ProviderCredentialUpdateRequest
    ) -> dict[str, Any]:
        _, runtime, authenticated, _ = await _require_bot_admin(request)
        try:
            credential = await runtime.secrets.update_credential(
                name,
                credential_id,
                expected_version=payload.version,
                updates=payload.model_dump(exclude={"version"}, exclude_none=True),
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return _credential_mutation_payload(credential)

    @router.post(
        "/{name}/credentials/{credential_id}/replace",
        response_model=ProviderCredentialMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin_step_up"},
        responses={**versioned_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def replace_provider_credential(
        request: Request, name: str, credential_id: int, payload: ProviderCredentialReplaceRequest
    ) -> dict[str, Any]:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_replace",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:replace", context=context)
        if len(payload.value) > context.settings.control_plane_max_secret_length:
            raise ControlPlaneAPIError(
                422, "credential_invalid", "The provider credential is invalid."
            )
        try:
            credential = await runtime.secrets.replace_credential(
                name,
                credential_id,
                expected_version=payload.version,
                value=payload.value,
                label=payload.label,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return _credential_mutation_payload(credential)

    @router.post(
        "/{name}/credentials/{credential_id}/reveal",
        response_model=ProviderCredentialRevealResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin_step_up"},
        responses={**common_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def reveal_provider_credential(
        request: Request, name: str, credential_id: int
    ) -> JSONResponse:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_reveal",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:reveal", context=context)
        try:
            value = await runtime.secrets.reveal_credential(
                name, credential_id, actor_user_id=authenticated.identity.telegram_user_id
            )
        except ValueError as exc:
            _raise_provider_error(exc)
        expires_at = datetime.now(UTC) + timedelta(seconds=30)
        return JSONResponse(
            {"credential_id": credential_id, "value": value, "expires_at": expires_at.isoformat()},
            headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"},
        )

    @router.post(
        "/{name}/credentials/{credential_id}/test",
        response_model=ProviderCredentialTestResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin"},
        responses={
            **common_errors,
            429: {"model": ControlPlaneErrorEnvelope},
            503: {"model": ControlPlaneErrorEnvelope},
        },
    )
    async def test_provider_credential(
        request: Request, name: str, credential_id: int, payload: ProviderCredentialTestRequest
    ) -> dict[str, Any]:
        context, _, _, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="provider_credential_test",
            limit=context.settings.control_plane_provider_test_rate_limit,
        )
        try:
            return await context.memory.provider_registry.test_managed_credential(
                name, credential_id, mode=payload.mode
            )
        except ProviderCallError as exc:
            raise ControlPlaneAPIError(
                503, "provider_credential_test_failed", "The provider credential check failed."
            ) from exc
        except ValueError as exc:
            _raise_provider_error(exc)

    @router.delete(
        "/{name}/credentials/{credential_id}",
        response_model=DeleteMutationResponse,
        openapi_extra={"x-chatterlore-capability": "bot_admin_step_up"},
        responses={**versioned_errors, 429: {"model": ControlPlaneErrorEnvelope}},
    )
    async def delete_provider_credential(
        request: Request,
        name: str,
        credential_id: int,
        payload: ProviderCredentialDeleteRequest = Body(),
    ) -> DeleteMutationResponse:
        context, runtime, authenticated, _ = await _require_bot_admin(request)
        _check_rate_limit(
            request,
            bucket="credential_delete",
            limit=context.settings.control_plane_step_up_rate_limit,
        )
        _require_step_up(authenticated, action="credential:delete", context=context)
        try:
            audit_event_id = await runtime.secrets.delete_credential(
                name,
                credential_id,
                expected_version=payload.version,
                actor_user_id=authenticated.identity.telegram_user_id,
                now=datetime.now(UTC),
            )
        except (ProviderSecretConflictError, ValueError) as exc:
            _raise_provider_error(exc)
        return DeleteMutationResponse(audit_event_id=audit_event_id, version=payload.version + 1)

    return router


def _profile_mutation_payload(profile: Any) -> dict[str, Any]:
    payload = profile.as_dict()
    payload["audit_event_id"] = int(profile.audit_event_id)
    return payload


def _credential_mutation_payload(credential: Any) -> dict[str, Any]:
    payload = credential.as_dict()
    payload["audit_event_id"] = int(credential.audit_event_id)
    return payload
