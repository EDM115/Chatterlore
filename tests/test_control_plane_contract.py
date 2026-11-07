from __future__ import annotations

import http.client
from typing import Any

import pytest

from pzp_ai_bot.app_context import AppContext
from pzp_ai_bot.control_plane.app import create_control_plane_schema_app
from pzp_ai_bot.telegram.webhook import create_webhook_app


def test_openapi_validation_descriptions_do_not_depend_on_python_status_phrases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(http.client.responses, 422, "Interpreter-specific status phrase")
    schema = create_control_plane_schema_app().openapi()
    descriptions = {
        operation["responses"]["422"]["description"]
        for path in schema["paths"].values()
        for operation in path.values()
        if "422" in operation.get("responses", {})
    }
    assert descriptions == {"Unprocessable Content", "Validation Error"}


AUTHORIZATION_MATRIX = {
    ("get", "/auth/telegram/start"): "public_oidc",
    ("get", "/auth/telegram/callback"): "public_oidc",
    ("get", "/auth/telegram/step-up/start"): "authenticated_same_identity",
    ("post", "/api/control/v1/auth/mini-app"): "telegram_identity",
    ("get", "/api/control/v1/session"): "authenticated",
    ("post", "/api/control/v1/auth/logout"): "authenticated",
    ("post", "/api/control/v1/auth/step-up/mini-app"): "authenticated_same_identity",
    ("get", "/api/control/v1/bot-settings/runtime"): "bot_admin",
    ("patch", "/api/control/v1/bot-settings/runtime"): "bot_admin",
    ("get", "/api/control/v1/bot-settings/admins"): "bot_admin",
    ("post", "/api/control/v1/bot-settings/admins"): "bot_admin",
    ("delete", "/api/control/v1/bot-settings/admins/{telegram_user_id}"): "bot_admin_step_up",
    ("get", "/api/control/v1/providers"): "bot_admin",
    ("post", "/api/control/v1/providers"): "bot_admin",
    ("get", "/api/control/v1/providers/{name}"): "bot_admin",
    ("patch", "/api/control/v1/providers/{name}"): "bot_admin",
    ("delete", "/api/control/v1/providers/{name}"): "bot_admin_step_up",
    ("get", "/api/control/v1/providers/{name}/credentials"): "bot_admin",
    ("get", "/api/control/v1/providers/{name}/pool"): "bot_admin",
    ("post", "/api/control/v1/providers/{name}/credentials"): "bot_admin_step_up",
    ("patch", "/api/control/v1/providers/{name}/credentials/{credential_id}"): "bot_admin",
    (
        "post",
        "/api/control/v1/providers/{name}/credentials/{credential_id}/replace",
    ): "bot_admin_step_up",
    (
        "post",
        "/api/control/v1/providers/{name}/credentials/{credential_id}/reveal",
    ): "bot_admin_step_up",
    ("post", "/api/control/v1/providers/{name}/credentials/{credential_id}/test"): "bot_admin",
    ("delete", "/api/control/v1/providers/{name}/credentials/{credential_id}"): "bot_admin_step_up",
    ("get", "/api/control/v1/chats"): "authenticated_admin",
    ("post", "/api/control/v1/chats/enrollment-requests"): "bot_admin",
    ("get", "/api/control/v1/chats/enrollment-requests/{request_id}"): "authenticated",
    ("patch", "/api/control/v1/chats/{chat_id}/status"): "chat_admin",
    ("get", "/api/control/v1/chats/{chat_id}/settings"): "chat_admin",
    ("patch", "/api/control/v1/chats/{chat_id}/settings"): "chat_admin",
    ("post", "/api/control/v1/chats/{chat_id}/leave"): "bot_admin_step_up",
    ("post", "/api/control/v1/chats/{chat_id}/forget"): "bot_admin_step_up",
    ("get", "/api/control/v1/chats/{chat_id}/admins"): "bot_admin",
    ("post", "/api/control/v1/chats/{chat_id}/admins"): "bot_admin",
    ("delete", "/api/control/v1/chats/{chat_id}/admins/{telegram_user_id}"): "bot_admin",
    ("get", "/api/control/v1/query/catalog"): "chat_admin",
    ("post", "/api/control/v1/query/search"): "chat_admin",
    ("post", "/api/control/v1/query/ask"): "chat_admin",
    ("get", "/api/control/v1/memory/items"): "scope_admin",
    ("get", "/api/control/v1/memory/items/{item_id}"): "persisted_scope_admin",
    ("post", "/api/control/v1/memory/items/{item_id}/sources"): "persisted_scope_admin",
    ("delete", "/api/control/v1/memory/items/{item_id}"): "persisted_scope_admin",
    ("post", "/api/control/v1/memory/items/{item_id}/refresh"): "persisted_scope_admin",
    ("get", "/api/control/v1/memory/reviews"): "scope_admin",
    ("post", "/api/control/v1/memory/reviews/{review_id}/approve"): "persisted_scope_admin",
    ("post", "/api/control/v1/memory/reviews/{review_id}/reject"): "persisted_scope_admin",
    ("get", "/api/control/v1/memory/export"): "scope_admin",
    ("get", "/api/control/v1/stats/overview"): "scope_admin",
    ("get", "/api/control/v1/stats/timeseries"): "scope_admin",
    ("get", "/api/control/v1/audit/events"): "scope_admin",
    ("get", "/api/control/v1/audit/ai-usage"): "scope_admin",
    ("get", "/api/control/v1/operations/health"): "bot_admin",
    ("post", "/api/control/v1/operations/checks/{name}"): "bot_admin",
    ("get", "/api/control/v1/alerts"): "bot_admin",
    ("post", "/api/control/v1/alerts/{alert_id}/retry"): "bot_admin",
}

PAGINATED_OPERATIONS = {
    ("get", "/api/control/v1/chats"),
    ("get", "/api/control/v1/memory/items"),
    ("get", "/api/control/v1/memory/reviews"),
    ("get", "/api/control/v1/audit/events"),
    ("get", "/api/control/v1/alerts"),
}

VERSIONED_MUTATIONS = {
    ("patch", "/api/control/v1/bot-settings/runtime"),
    ("patch", "/api/control/v1/providers/{name}"),
    ("patch", "/api/control/v1/providers/{name}/credentials/{credential_id}"),
    ("patch", "/api/control/v1/chats/{chat_id}/status"),
    ("patch", "/api/control/v1/chats/{chat_id}/settings"),
}

RATE_LIMITED_OPERATIONS = {
    ("get", "/auth/telegram/start"),
    ("get", "/auth/telegram/callback"),
    ("get", "/auth/telegram/step-up/start"),
    ("post", "/api/control/v1/auth/mini-app"),
    ("post", "/api/control/v1/auth/step-up/mini-app"),
    ("delete", "/api/control/v1/providers/{name}"),
    ("post", "/api/control/v1/providers/{name}/credentials"),
    ("post", "/api/control/v1/providers/{name}/credentials/{credential_id}/reveal"),
    ("post", "/api/control/v1/providers/{name}/credentials/{credential_id}/test"),
    ("delete", "/api/control/v1/providers/{name}/credentials/{credential_id}"),
    ("post", "/api/control/v1/query/search"),
    ("post", "/api/control/v1/query/ask"),
    ("get", "/api/control/v1/memory/export"),
}

VALIDATED_OPERATIONS = {
    (method, path)
    for method, path in AUTHORIZATION_MATRIX
    if method != "get" and path != "/api/control/v1/auth/logout"
}

SAFE_CREDENTIAL_PROPERTIES = {
    "id",
    "profile_name",
    "label",
    "source",
    "environment_variable",
    "last_four",
    "fingerprint",
    "position",
    "state",
    "health_state",
    "cooldown_until",
    "last_error_classification",
    "success_count",
    "failure_count",
    "last_success_at",
    "last_failure_at",
    "version",
    "revealable",
    "active",
}

FORBIDDEN_CREDENTIAL_PROPERTIES = {
    "api_key",
    "ciphertext",
    "encrypted_secret",
    "nonce",
    "secret",
    "value",
    "wrapped_dek",
    "wrap_nonce",
}


def _openapi(context: AppContext) -> dict[str, Any]:
    if context.control_plane is None:
        return create_control_plane_schema_app().openapi()
    return create_webhook_app(context).openapi()


def _operation(openapi: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    assert path in openapi["paths"], f"missing control-plane path: {path}"
    assert method in openapi["paths"][path], f"missing {method.upper()} operation for {path}"
    return openapi["paths"][path][method]


def _schema(openapi: dict[str, Any], name: str) -> dict[str, Any]:
    schemas = openapi.get("components", {}).get("schemas", {})
    assert name in schemas, f"missing control-plane schema: {name}"
    return schemas[name]


def _response_schema(
    openapi: dict[str, Any], operation: dict[str, Any], status: str
) -> dict[str, Any]:
    response = operation["responses"][status]
    schema = response["content"]["application/json"]["schema"]
    reference = schema.get("$ref")
    if reference:
        return _schema(openapi, reference.rsplit("/", maxsplit=1)[-1])
    return schema


def test_control_plane_openapi_declares_every_reviewed_route_and_capability(
    context: AppContext,
) -> None:
    openapi = _openapi(context)
    actual_operations = {
        (method, path)
        for path, path_item in openapi["paths"].items()
        for method in path_item
        if method in {"delete", "get", "patch", "post", "put"}
    }

    assert actual_operations == set(AUTHORIZATION_MATRIX)
    for (method, path), capability in AUTHORIZATION_MATRIX.items():
        operation = _operation(openapi, method, path)
        assert operation["x-pzp-capability"] == capability


def test_control_plane_list_contract_uses_bounded_cursor_pagination(context: AppContext) -> None:
    openapi = _openapi(context)

    for method, path in PAGINATED_OPERATIONS:
        operation = _operation(openapi, method, path)
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["cursor"]["in"] == "query"
        assert parameters["limit"]["in"] == "query"
        assert parameters["limit"]["schema"]["minimum"] == 1
        assert parameters["limit"]["schema"]["maximum"] == 100
        response_schema = _response_schema(openapi, operation, "200")
        assert {"items", "next_cursor", "has_more"} <= set(response_schema["required"])
        assert response_schema["properties"]["items"]["type"] == "array"
        assert response_schema["properties"]["has_more"]["type"] == "boolean"


def test_control_plane_errors_and_version_conflicts_have_one_safe_envelope(
    context: AppContext,
) -> None:
    openapi = _openapi(context)
    error_detail = _schema(openapi, "ControlPlaneErrorDetail")
    error_envelope = _schema(openapi, "ControlPlaneErrorEnvelope")
    conflict_envelope = _schema(openapi, "ControlPlaneConflictEnvelope")

    assert set(error_detail["required"]) == {"code", "message"}
    assert set(error_detail["properties"]) == {"code", "message", "field_errors", "request_id"}
    assert set(error_envelope["required"]) == {"error"}
    assert set(conflict_envelope["required"]) == {"error", "current"}

    for method, path in AUTHORIZATION_MATRIX:
        operation = _operation(openapi, method, path)
        if path.startswith("/api/control/v1/"):
            assert "401" in operation["responses"]
            assert _response_schema(openapi, operation, "401") == error_envelope
        if AUTHORIZATION_MATRIX[(method, path)] not in {
            "public_oidc",
            "telegram_identity",
            "authenticated",
            "authenticated_same_identity",
        }:
            assert "403" in operation["responses"]
            assert _response_schema(openapi, operation, "403") == error_envelope
        if (method, path) in VALIDATED_OPERATIONS:
            assert "422" in operation["responses"]
            assert _response_schema(openapi, operation, "422") == error_envelope
        if (method, path) in RATE_LIMITED_OPERATIONS:
            assert "429" in operation["responses"]
            assert _response_schema(openapi, operation, "429") == error_envelope

    for method, path in VERSIONED_MUTATIONS:
        operation = _operation(openapi, method, path)
        assert "409" in operation["responses"]
        assert _response_schema(openapi, operation, "409") == conflict_envelope


def test_control_plane_versioned_mutations_require_and_return_versions(context: AppContext) -> None:
    openapi = _openapi(context)

    for method, path in VERSIONED_MUTATIONS:
        operation = _operation(openapi, method, path)
        request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        request_name = request_schema["$ref"].rsplit("/", maxsplit=1)[-1]
        request_model = _schema(openapi, request_name)
        assert "version" in request_model["required"]
        assert request_model["properties"]["version"]["type"] == "integer"
        response_model = _response_schema(openapi, operation, "200")
        assert {"audit_event_id", "version"} <= response_model["properties"].keys()
        assert {"audit_event_id", "version"} <= set(response_model["required"])


def test_provider_credentials_are_masked_except_for_the_isolated_reveal_response(
    context: AppContext,
) -> None:
    openapi = _openapi(context)
    credential = _schema(openapi, "ProviderCredentialSummary")
    reveal = _schema(openapi, "ProviderCredentialRevealResponse")

    assert set(credential["properties"]) == SAFE_CREDENTIAL_PROPERTIES
    assert not FORBIDDEN_CREDENTIAL_PROPERTIES.intersection(credential["properties"])
    assert set(reveal["required"]) == {"credential_id", "value", "expires_at"}
    assert set(reveal["properties"]) == {"credential_id", "value", "expires_at"}
    assert "example" not in reveal["properties"]["value"]

    serialized = str(openapi)
    assert "sk-" not in serialized
    assert "Bearer " not in serialized
