from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from pzp_ai_bot.ai.credential_pool import (
    CredentialPoolService,
    ProviderCallError,
    ProviderPoolExhaustedError,
)
from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.ai.failure_classification import (
    MAX_RETRY_AFTER_SECONDS,
    ProviderFailureClassification,
    classify_provider_failure,
)
from pzp_ai_bot.ai.protocols import ChatMessage
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.secrets.service import ProviderSecretService
from pzp_ai_bot.db.repos.alerts import AdminAlertRepository
from pzp_ai_bot.db.repos.provider_credentials import (
    ProviderCredentialRepository,
    ProviderProfileRepository,
)
from pzp_ai_bot.db.session import create_schema, create_session_factory, session_scope
from pzp_ai_bot.memory.provider_operations import ProviderOperations

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def status_error(
    error_type: type[APIStatusError],
    status_code: int,
    *,
    code: str | None = None,
    retry_after: str | None = None,
) -> APIStatusError:
    request = httpx.Request("POST", "https://provider.example.test/v1/responses")
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    response = httpx.Response(status_code, request=request, headers=headers)
    body = {"error": {"code": code, "message": "PRIVATE PROVIDER BODY"}}
    return error_type("PRIVATE PROVIDER ERROR", response=response, body=body)


@pytest.mark.parametrize(
    ("error", "classification"),
    [
        (status_error(BadRequestError, 400), ProviderFailureClassification.INVALID_REQUEST),
        (status_error(NotFoundError, 404), ProviderFailureClassification.INVALID_REQUEST),
        (
            status_error(UnprocessableEntityError, 422),
            ProviderFailureClassification.INVALID_REQUEST,
        ),
        (status_error(AuthenticationError, 401), ProviderFailureClassification.AUTHENTICATION),
        (status_error(PermissionDeniedError, 403), ProviderFailureClassification.PERMISSION),
        (status_error(InternalServerError, 503), ProviderFailureClassification.PROVIDER),
        (status_error(APIStatusError, 408), ProviderFailureClassification.PROVIDER),
        (status_error(APIStatusError, 409), ProviderFailureClassification.PROVIDER),
        (status_error(APIStatusError, 451), ProviderFailureClassification.INVALID_REQUEST),
        (
            APITimeoutError(httpx.Request("POST", "https://provider.example.test")),
            ProviderFailureClassification.TRANSPORT,
        ),
        (
            APIConnectionError(request=httpx.Request("POST", "https://provider.example.test")),
            ProviderFailureClassification.TRANSPORT,
        ),
        (RuntimeError("PRIVATE UNKNOWN ERROR"), ProviderFailureClassification.UNKNOWN),
    ],
)
def test_classifier_covers_the_sdk_table_without_exposing_provider_content(
    error: Exception, classification: ProviderFailureClassification
) -> None:
    decision = classify_provider_failure(error, now=NOW)

    assert decision.classification is classification
    assert "PRIVATE" not in repr(decision)
    if classification is ProviderFailureClassification.INVALID_REQUEST:
        assert decision.rotate is False
        assert decision.state_transition is None
    if classification in {
        ProviderFailureClassification.TRANSPORT,
        ProviderFailureClassification.PROVIDER,
    }:
        assert decision.same_key_retry is True
        assert decision.rotate is True
        assert decision.state_transition == "cooldown"


def test_classifier_allowlists_quota_and_bounds_retry_after() -> None:
    quota = classify_provider_failure(
        status_error(RateLimitError, 429, code="insufficient_quota"), now=NOW
    )
    rate_limited = classify_provider_failure(
        status_error(RateLimitError, 429, code="requests", retry_after="999999"), now=NOW
    )

    assert quota.classification is ProviderFailureClassification.QUOTA
    assert quota.state_transition == "disabled"
    assert quota.rotate is True
    assert quota.same_key_retry is False
    assert rate_limited.classification is ProviderFailureClassification.RATE_LIMIT
    assert rate_limited.state_transition == "cooldown"
    assert rate_limited.retry_after_seconds == MAX_RETRY_AFTER_SECONDS
    assert rate_limited.rotate is True
    assert rate_limited.same_key_retry is False


def test_classifier_does_not_treat_unallowlisted_provider_codes_as_quota() -> None:
    decision = classify_provider_failure(
        status_error(RateLimitError, 429, code="billing_hard_limit_reached", retry_after="invalid"),
        now=NOW,
    )

    assert decision.classification is ProviderFailureClassification.RATE_LIMIT
    assert decision.retry_after_seconds is not None
    assert 0 < decision.retry_after_seconds <= MAX_RETRY_AFTER_SECONDS


def pool_settings(tmp_path: Path, *, credential_limit: int = 10) -> Settings:
    master_key = base64.b64encode(bytes([11]) * 32).decode()
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
        memory_dir=tmp_path / "memory",
        ai_provider="fake",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
        control_plane_master_keys=json.dumps({"primary": master_key}),
        control_plane_active_master_key_id="primary",
        control_plane_max_credentials_per_profile=credential_limit,
        provider_retry_attempts=3,
        provider_retry_backoff_seconds=0,
    )


async def create_pool(
    tmp_path: Path, *, labels: tuple[str, ...] = ("first", "second")
) -> tuple[CredentialPoolService, Settings]:
    settings = pool_settings(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    secrets = ProviderSecretService(settings, factory, ProviderRegistry(settings))
    await secrets.create_profile(
        {
            "name": "pooled",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    for index, label in enumerate(labels):
        await secrets.create_credential(
            "pooled", label=label, value=f"credential-secret-{index}", actor_user_id=111, now=NOW
        )
    return (CredentialPoolService(settings, factory, secrets.vault, clock=lambda: NOW), settings)


@pytest.mark.asyncio
async def test_pool_rotates_once_then_keeps_the_successful_key_sticky_across_restart(
    tmp_path: Path,
) -> None:
    pool, settings = await create_pool(tmp_path)
    labels: list[str] = []

    async def fail_first(lease):
        labels.append(lease.label)
        if lease.label == "first":
            raise status_error(AuthenticationError, 401)
        return "ok"

    first = await pool.execute("pooled", fail_first)
    restarted = CredentialPoolService(
        settings, create_session_factory(settings), pool.vault, clock=lambda: NOW
    )
    second = await restarted.execute("pooled", fail_first)

    assert first.value == "ok"
    assert first.metadata.attempted_credentials == 2
    assert first.metadata.rotation_count == 1
    assert first.metadata.final_classification == "success"
    assert second.value == "ok"
    assert second.metadata.attempted_credentials == 1
    assert labels == ["first", "second", "second"]

    async with session_scope(create_session_factory(settings)) as session:
        profile = await ProviderProfileRepository(session).get_by_name("pooled")
        assert profile is not None
        credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
        state = await ProviderProfileRepository(session).get_pool_state(profile.id)
    assert [(item.label, item.health_state) for item in credentials] == [
        ("first", "disabled"),
        ("second", "healthy"),
    ]
    assert state is not None
    assert state.active_credential_id == credentials[1].id


@pytest.mark.asyncio
async def test_pool_retries_transport_failures_on_the_same_key_before_rotating(
    tmp_path: Path,
) -> None:
    pool, _ = await create_pool(tmp_path)
    calls: list[str] = []

    async def eventually_succeeds(lease):
        calls.append(lease.label)
        if len(calls) < 3:
            raise APIConnectionError(request=httpx.Request("POST", "https://provider.example.test"))
        return 42

    outcome = await pool.execute("pooled", eventually_succeeds)

    assert outcome.value == 42
    assert calls == ["first", "first", "first"]
    assert outcome.metadata.same_key_attempts == 3
    assert outcome.metadata.attempted_credentials == 1
    assert outcome.metadata.rotation_count == 0


@pytest.mark.asyncio
async def test_invalid_request_never_rotates_or_degrades_a_credential(tmp_path: Path) -> None:
    pool, settings = await create_pool(tmp_path)

    async def invalid_request(_lease):
        raise status_error(BadRequestError, 400)

    with pytest.raises(ProviderCallError) as caught:
        await pool.execute("pooled", invalid_request)

    assert caught.value.classification == "invalid_request"
    assert caught.value.metadata.attempted_credentials == 1
    assert caught.value.metadata.rotation_count == 0
    async with session_scope(create_session_factory(settings)) as session:
        profile = await ProviderProfileRepository(session).get_by_name("pooled")
        assert profile is not None
        credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
    assert [(item.health_state, item.failure_count) for item in credentials] == [
        ("unknown", 0),
        ("unknown", 0),
    ]


@pytest.mark.asyncio
async def test_pool_exhaustion_attempts_each_eligible_key_once(tmp_path: Path) -> None:
    pool, _ = await create_pool(tmp_path, labels=("first", "second", "third"))
    labels: list[str] = []

    async def dead_key(lease):
        labels.append(lease.label)
        raise status_error(AuthenticationError, 401)

    with pytest.raises(ProviderPoolExhaustedError) as caught:
        await pool.execute("pooled", dead_key)

    assert labels == ["first", "second", "third"]
    assert caught.value.profile_name == "pooled"
    assert caught.value.attempted_count == 3
    assert caught.value.earliest_retry_at is None
    assert "credential" not in str(caught.value).lower()


@pytest.mark.asyncio
async def test_concurrent_failures_produce_one_health_transition_and_one_rotation(
    tmp_path: Path,
) -> None:
    pool, settings = await create_pool(tmp_path)
    callers = 12
    selected_first = 0
    all_selected = asyncio.Event()

    async def concurrent_call(lease):
        nonlocal selected_first
        if lease.label == "first":
            selected_first += 1
            if selected_first == callers:
                all_selected.set()
            await asyncio.wait_for(all_selected.wait(), timeout=5)
            raise status_error(AuthenticationError, 401)
        return lease.label

    results = await asyncio.gather(
        *(pool.execute("pooled", concurrent_call) for _ in range(callers))
    )

    assert selected_first == callers
    assert [result.value for result in results] == ["second"] * callers
    assert all(result.metadata.rotation_count == 1 for result in results)
    async with session_scope(create_session_factory(settings)) as session:
        profile = await ProviderProfileRepository(session).get_by_name("pooled")
        assert profile is not None
        credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
        state = await ProviderProfileRepository(session).get_pool_state(profile.id)
    assert credentials[0].health_state == "disabled"
    assert credentials[0].failure_count == 1
    assert credentials[1].health_state == "healthy"
    assert state is not None
    assert state.health_epoch == 1
    assert state.active_credential_id == credentials[1].id


@pytest.mark.asyncio
async def test_concurrent_pool_instances_use_database_versions_as_the_rotation_authority(
    tmp_path: Path,
) -> None:
    first_pool, settings = await create_pool(tmp_path)
    second_pool = CredentialPoolService(
        settings, create_session_factory(settings), first_pool.vault, clock=lambda: NOW
    )
    selected_first = 0
    both_selected = asyncio.Event()

    async def concurrent_call(lease):
        nonlocal selected_first
        if lease.label == "first":
            selected_first += 1
            if selected_first == 2:
                both_selected.set()
            await asyncio.wait_for(both_selected.wait(), timeout=5)
            raise status_error(AuthenticationError, 401)
        return lease.label

    results = await asyncio.gather(
        first_pool.execute("pooled", concurrent_call),
        second_pool.execute("pooled", concurrent_call),
    )

    assert [result.value for result in results] == ["second", "second"]
    async with session_scope(create_session_factory(settings)) as session:
        profile = await ProviderProfileRepository(session).get_by_name("pooled")
        assert profile is not None
        credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
        state = await ProviderProfileRepository(session).get_pool_state(profile.id)
    assert credentials[0].failure_count == 1
    assert credentials[0].health_state == "disabled"
    assert state is not None
    assert state.health_epoch == 1


@pytest.mark.asyncio
async def test_expired_cooldown_recovers_the_key_and_emits_one_recovery_epoch(
    tmp_path: Path,
) -> None:
    original, settings = await create_pool(tmp_path, labels=("only",))
    clock = [NOW]
    pool = CredentialPoolService(
        settings, create_session_factory(settings), original.vault, clock=lambda: clock[0]
    )

    async def transport_failure(_lease):
        raise APIConnectionError(request=httpx.Request("POST", "https://provider.example.test"))

    with pytest.raises(ProviderPoolExhaustedError):
        await pool.execute("pooled", transport_failure)

    clock[0] = NOW.replace(minute=NOW.minute + 2)
    recovered = await pool.execute("pooled", lambda lease: asyncio.sleep(0, result=lease.label))

    assert recovered.value == "only"
    summary = await pool.summary("pooled")
    assert summary.eligible_credentials == 1
    assert summary.cooldown_credentials == 0
    assert summary.active_credential_label == "only"
    assert summary.last_recovered_at is not None
    async with session_scope(create_session_factory(settings)) as session:
        alerts = await AdminAlertRepository(session).list_recent(limit=100)
        profile = await ProviderProfileRepository(session).get_by_name("pooled")
        assert profile is not None
        credential = (await ProviderCredentialRepository(session).list_for_profile(profile.id))[0]
    assert credential.health_state == "healthy"
    assert credential.success_count == 1
    assert [alert.alert_type for alert in reversed(alerts)] == [
        "credential_cooldown",
        "pool_exhausted",
        "credential_recovered",
    ]


@pytest.mark.asyncio
async def test_environment_bootstrap_key_is_persisted_as_non_revealable_pool_metadata(
    tmp_path: Path,
) -> None:
    settings = pool_settings(tmp_path, credential_limit=2).model_copy(
        update={
            "ai_provider": "openai_compatible",
            "openai_api_key": "environment-secret",
            "openai_api_key_env": "OPENAI_API_KEY",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-small",
        }
    )
    await create_schema(settings)
    factory = create_session_factory(settings)
    secrets = ProviderSecretService(settings, factory, ProviderRegistry(settings))
    await secrets.create_profile(
        {
            "name": "default",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    await secrets.create_credential(
        "default", label="managed", value="managed-secret", actor_user_id=111, now=NOW
    )
    pool = CredentialPoolService(settings, factory, secrets.vault, clock=lambda: NOW)
    labels: list[str] = []

    async def rotate_from_environment(lease):
        labels.append(lease.label)
        if lease.source == "environment":
            assert lease.secret_value() == "environment-secret"
            raise status_error(AuthenticationError, 401)
        assert lease.secret_value() == "managed-secret"
        return "ok"

    outcome = await pool.execute("default", rotate_from_environment)
    summaries = await secrets.list_credentials("default")

    assert outcome.value == "ok"
    assert labels == ["OPENAI_API_KEY", "managed"]
    assert [item.source for item in summaries] == ["environment", "managed"]
    assert summaries[0].last_four is None
    assert summaries[0].revealable is False
    with pytest.raises(ValueError, match="credential limit"):
        await secrets.create_credential(
            "default", label="over-limit", value="unused-secret", actor_user_id=111, now=NOW
        )


@pytest.mark.asyncio
async def test_registry_routes_managed_chat_and_embedding_through_the_pool_with_safe_telemetry(
    tmp_path: Path,
) -> None:
    settings = pool_settings(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    calls: list[tuple[str, str]] = []

    def client_factory(api_key: str, _base_url: str):
        async def chat_create(**_kwargs):
            calls.append((api_key, "chat"))
            if api_key == "credential-secret-0":
                raise status_error(AuthenticationError, 401)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="pooled answer"))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            )

        async def embedding_create(**_kwargs):
            calls.append((api_key, "embedding"))
            if api_key == "credential-secret-0":
                raise status_error(AuthenticationError, 401)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
            embeddings=SimpleNamespace(create=embedding_create),
        )

    registry = ProviderRegistry(settings, openai_client_factory=client_factory)
    secrets = ProviderSecretService(settings, factory, registry)
    pool = CredentialPoolService(settings, factory, secrets.vault, clock=lambda: NOW)
    registry.configure_credential_pool(pool)
    await secrets.create_profile(
        {
            "name": "pooled",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    for index, label in enumerate(("first", "second")):
        await secrets.create_credential(
            "pooled", label=label, value=f"credential-secret-{index}", actor_user_id=111, now=NOW
        )

    selection = registry.select("pooled")
    operations = ProviderOperations()
    chat = await operations.execute_chat(
        selection,
        [ChatMessage(role="user", content="hello")],
        operation="ask",
        metadata={"scope": "-1001:main"},
        temperature=0.2,
        max_tokens=32,
        reasoning_effort="medium",
    )
    embedding = await operations.generate_embeddings(
        selection, ["hello"], operation="embed_query", metadata={"scope": "-1001:main"}
    )

    assert chat.result is not None
    assert chat.result.content == "pooled answer"
    assert embedding.vectors == [[0.1, 0.2, 0.3]]
    assert calls == [
        ("credential-secret-0", "chat"),
        ("credential-secret-1", "chat"),
        ("credential-secret-1", "embedding"),
    ]
    chat_metadata = dict(chat.telemetry.metadata)
    embedding_metadata = dict(embedding.telemetry.metadata)
    assert chat_metadata["managed_profile_revision"] == 1
    assert chat_metadata["credential_label"] == "second"
    assert chat_metadata["attempted_credential_count"] == 2
    assert chat_metadata["rotation_count"] == 1
    assert chat_metadata["final_classification"] == "success"
    assert embedding_metadata["credential_label"] == "second"
    assert embedding_metadata["attempted_credential_count"] == 1
    serialized = json.dumps([chat_metadata, embedding_metadata])
    assert "credential-secret" not in serialized
    assert "credential_id" not in serialized


@pytest.mark.asyncio
async def test_registry_invalidation_prevents_use_of_a_deleted_cached_credential(
    tmp_path: Path,
) -> None:
    settings = pool_settings(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    used_keys: list[str] = []

    def client_factory(api_key: str, _base_url: str):
        async def embedding_create(**_kwargs):
            used_keys.append(api_key)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0])])

        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace()),
            embeddings=SimpleNamespace(create=embedding_create),
        )

    registry = ProviderRegistry(settings, openai_client_factory=client_factory)
    secrets = ProviderSecretService(settings, factory, registry)
    pool = CredentialPoolService(settings, factory, secrets.vault, clock=lambda: NOW)
    registry.configure_credential_pool(pool)
    profile = await secrets.create_profile(
        {
            "name": "pooled",
            "provider_kind": "openai_compatible",
            "base_url": "https://api.openai.com/v1",
            "chat_model": "gpt-5.6-terra",
            "embedding_model": "text-embedding-3-small",
            "chat_capable": True,
            "embedding_capable": True,
            "reasoning_capable": True,
            "local": False,
            "redact_before_ai": True,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 64,
        },
        actor_user_id=111,
        now=NOW,
    )
    first = await secrets.create_credential(
        "pooled", label="first", value="first-secret", actor_user_id=111, now=NOW
    )
    await registry.select("pooled").embedding_provider.embed(["before delete"])
    await secrets.delete_credential(
        "pooled", int(first.id), expected_version=first.version + 1, actor_user_id=111, now=NOW
    )
    await secrets.create_credential(
        "pooled", label="replacement", value="replacement-secret", actor_user_id=111, now=NOW
    )
    await registry.select("pooled").embedding_provider.embed(["after delete"])

    assert profile.version == 1
    assert used_keys == ["first-secret", "replacement-secret"]


@pytest.mark.asyncio
async def test_pool_commits_deduplicated_safe_alert_intents_without_changing_provider_results(
    tmp_path: Path,
) -> None:
    pool, settings = await create_pool(tmp_path)

    async def first_key_dead(lease):
        if lease.label == "first":
            raise status_error(AuthenticationError, 401)
        return "served"

    successful = await pool.execute("pooled", first_key_dead)

    assert successful.value == "served"
    async with session_scope(create_session_factory(settings)) as session:
        first_alerts = await AdminAlertRepository(session).list_recent(limit=100)
    assert [alert.alert_type for alert in first_alerts] == ["credential_disabled"]
    assert first_alerts[0].payload == {"classification": "authentication"}

    async def every_remaining_key_dead(_lease):
        raise status_error(AuthenticationError, 401)

    with pytest.raises(ProviderPoolExhaustedError):
        await pool.execute("pooled", every_remaining_key_dead)
    with pytest.raises(ProviderPoolExhaustedError):
        await pool.execute("pooled", every_remaining_key_dead)

    async with session_scope(create_session_factory(settings)) as session:
        alerts = await AdminAlertRepository(session).list_recent(limit=100)
    assert sorted(alert.alert_type for alert in alerts) == [
        "credential_disabled",
        "credential_disabled",
        "pool_exhausted",
    ]
    serialized = json.dumps(
        [
            {
                "type": alert.alert_type,
                "profile": alert.profile_name,
                "fingerprint": alert.credential_fingerprint,
                "label": alert.credential_label,
                "payload": alert.payload,
            }
            for alert in alerts
        ]
    )
    assert "credential-secret" not in serialized
    assert "PRIVATE" not in serialized
