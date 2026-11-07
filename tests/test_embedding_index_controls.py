from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Bot, Dispatcher, Router
from fastapi.testclient import TestClient
from sqlalchemy import select
from typer.testing import CliRunner

from pzp_ai_bot.control_plane.secrets.service import ProviderSecretService
from pzp_ai_bot.db.models import AIUsage
from pzp_ai_bot.db.repos.control_plane import ControlPlaneAccessRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.main import app
from pzp_ai_bot.memory.scope import Scope
from test_control_plane_api import ORIGIN, build_test_app, signed_init_data
from test_telegram_integration import RecordingTelegramSession, feed, message_update

ROOT = "/api/control/v1/embedding-indexes"


async def install_local_profile(context, model="fixture/model", options=None):
    service = (
        context.control_plane.secrets
        if context.control_plane
        else ProviderSecretService(
            context.settings, context.session_factory, context.memory.provider_registry
        )
    )
    await service.create_profile(
        {
            "name": "local",
            "provider_kind": "local",
            "base_url": "http://localhost:11434/v1",
            "chat_model": model,
            "embedding_model": "",
            "chat_capable": True,
            "embedding_capable": False,
            "reasoning_capable": False,
            "local": True,
            "redact_before_ai": False,
            "redaction_patterns": [],
            "fake_embedding_dimensions": 8,
            "local_chat_options": options or {},
            "auth_mode": "none",
        },
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    return service


class IndexServiceDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.error: ValueError | None = None

    async def worker_tick(self) -> None:
        return None

    def job(self) -> dict[str, Any]:
        return {
            "id": 7,
            "scope": "all",
            "provider_profile": "default",
            "model": "fake-embedding",
            "space_id": None,
            "status": "queued",
            "version": 3,
            "processed": 0,
            "total": 4,
            "error_code": None,
            "created_at": "2026-11-07T12:00:00Z",
            "updated_at": "2026-11-07T12:00:00Z",
        }

    async def status(self, scope: Scope) -> dict[str, Any]:
        self.calls.append(("status", scope))
        return {"scope": "all", "active": None, "spaces": [], "jobs": [self.job()]}

    async def preview(
        self, scope: Scope, provider_profile: str, model: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("preview", (scope, provider_profile, model)))
        return {
            "scope": "all",
            "provider_profile": provider_profile,
            "model": model or "fake-embedding",
            "eligible": 4,
            "indexed": 0,
            "missing": 4,
            "cost_warning": None,
            "local_preparation_required": False,
        }

    async def create_job(
        self,
        scope: Scope,
        provider_profile: str,
        model: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("start", (scope, provider_profile, model, actor_user_id)))
        return self.job()

    async def get_job(self, job_id: int) -> dict[str, Any]:
        self.calls.append(("get", job_id))
        if self.error:
            raise self.error
        return self.job()

    async def run_job(self, job_id: int, max_batches: int = 1) -> dict[str, Any]:
        self.calls.append(("run", (job_id, max_batches)))
        return self.job()

    async def resume_job(
        self, job_id: int, version: int, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        self.calls.append(("resume", (job_id, version, actor_user_id)))
        if self.error:
            raise self.error
        return self.job()

    async def cancel_job(
        self, job_id: int, version: int, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        self.calls.append(("cancel", (job_id, version, actor_user_id)))
        return self.job()

    async def activate_job(
        self, job_id: int, version: int, confirmation: str, actor_user_id: int | None = None
    ) -> dict[str, Any]:
        self.calls.append(("activate", (job_id, version, confirmation, actor_user_id)))
        return self.job()


@pytest.fixture
def controls(tmp_path):
    context, api = build_test_app(tmp_path, ai_provider="fake")
    indexes = IndexServiceDouble()
    context.memory.embedding_indexes = indexes
    with TestClient(api, base_url=ORIGIN) as client:
        yield context, indexes, client
    asyncio.run(context.memory.provider_registry.aclose())
    asyncio.run(context.session_factory.kw["bind"].dispose())


def login(client: TestClient, user_id: int = 111) -> dict[str, str]:
    response = client.post(
        "/api/control/v1/auth/mini-app",
        headers={"Origin": ORIGIN},
        json={"init_data": signed_init_data(user_id)},
    )
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_owner_index_reads_are_metadata_only(controls) -> None:
    _, indexes, client = controls
    assert client.get(ROOT).status_code == 401
    login(client)
    status = client.get(ROOT)
    assert status.status_code == 200
    assert status.json()["jobs"][0]["total"] == 4
    preview = client.get(f"{ROOT}/preview", params={"scope": "all", "provider_profile": "default"})
    assert preview.status_code == 200
    assert preview.json()["missing"] == 4
    assert [name for name, _ in indexes.calls] == ["status", "preview"]


@pytest.mark.parametrize("user_id", [222, 333])
def test_nonowners_cannot_inspect_or_run_indexes_or_prepare_models(controls, user_id: int) -> None:
    context, indexes, client = controls

    async def grant_scoped_access():
        async with session_scope(context.session_factory) as session:
            repository = ControlPlaneAccessRepository(session)
            await repository.upsert_managed_chat(
                chat_id=-1001,
                title="Scoped access fixture",
                username=None,
                status="active",
                actor_user_id=111,
                now=datetime.now(UTC),
            )
            await repository.grant_chat_admin(
                chat_id=-1001, telegram_user_id=user_id, actor_user_id=111, now=datetime.now(UTC)
            )

    asyncio.run(grant_scoped_access())
    assert asyncio.run(
        context.control_plane.authorization.resolve(user_id)
    ).delegated_chat_ids == frozenset({-1001})
    headers = login(client, user_id)
    for path in (ROOT, f"{ROOT}/preview?provider_profile=default", f"{ROOT}/jobs/7"):
        assert client.get(path).status_code == 403
    for path, payload in (
        (f"{ROOT}/jobs", {"scope": "all", "provider_profile": "default"}),
        (f"{ROOT}/jobs/7/run", {"max_batches": 1}),
        (f"{ROOT}/jobs/7/resume", {"version": 3}),
        (f"{ROOT}/jobs/7/cancel", {"version": 3}),
        (f"{ROOT}/jobs/7/activate", {"version": 3, "confirmation": "ACTIVATE 7"}),
        (
            "/api/control/v1/providers/default/prepare-local",
            {"mode": "chat", "allow_download": True},
        ),
    ):
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "bot_admin_required"
    assert indexes.calls == []
    assert (
        client.post(
            "/api/control/v1/providers/default/test", headers=headers, json={"mode": "chat"}
        ).status_code
        == 403
    )


def test_index_mutations_require_csrf_and_validate_work_bounds_before_service(controls) -> None:
    _, indexes, client = controls
    headers = login(client)
    assert (
        client.post(
            f"{ROOT}/jobs", json={"scope": "all", "provider_profile": "default"}
        ).status_code
        == 403
    )
    for payload in ({"max_batches": 0}, {"max_batches": 11}, {"max_batches": True}):
        assert client.post(f"{ROOT}/jobs/7/run", headers=headers, json=payload).status_code == 422
    for version in (0, -1, True, "3"):
        assert (
            client.post(
                f"{ROOT}/jobs/7/resume", headers=headers, json={"version": version}
            ).status_code
            == 422
        )
    assert client.get(f"{ROOT}?scope=broken").status_code == 422
    assert indexes.calls == []
    assert (
        client.post(f"{ROOT}/jobs/7/run", headers=headers, json={"max_batches": 2}).status_code
        == 200
    )
    assert indexes.calls == [("run", (7, 2))]


def test_index_activation_requires_exact_phrase_and_action_bound_fresh_verification(
    controls,
) -> None:
    context, indexes, client = controls
    headers = login(client)
    payload = {"version": 3, "confirmation": "ACTIVATE 7"}
    response = client.post(f"{ROOT}/jobs/7/activate", headers=headers, json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "step_up_required"
    asyncio.run(
        context.control_plane.sessions.mark_step_up(
            client.cookies["pzp_control_session"],
            now=datetime.now(UTC),
            action="embedding:activate",
        )
    )
    for phrase in ("7", "activate", "ACTIVATE 8"):
        assert (
            client.post(
                f"{ROOT}/jobs/7/activate",
                headers=headers,
                json={"version": 3, "confirmation": phrase},
            ).status_code
            == 422
        )
    assert indexes.calls == []
    assert client.post(f"{ROOT}/jobs/7/activate", headers=headers, json=payload).status_code == 200
    assert indexes.calls == [("activate", (7, 3, "ACTIVATE 7", 111))]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        ("embedding_job_not_found secret-prompt", 404, "embedding_job_not_found"),
        ("embedding_job_version_conflict secret-prompt", 409, "embedding_job_version_conflict"),
        ("secret-key-and-prompt", 422, "embedding_request_invalid"),
    ],
)
def test_index_errors_are_classified_without_exception_text(
    controls, error: str, status: int, code: str
) -> None:
    _, indexes, client = controls
    login(client)
    indexes.error = ValueError(error)
    response = client.get(f"{ROOT}/jobs/7")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "secret" not in response.text


def test_local_prepare_never_downloads_by_default_and_download_requires_separate_stepup(
    controls,
) -> None:
    context, _, client = controls
    prepared: list[bool] = []

    async def prepare(*, allow_download=False, **kwargs):
        prepared.append(allow_download)

    model_path = context.settings.memory_dir.parent / "models" / "fixture"
    model_path.mkdir(parents=True)
    (model_path / "model.safetensors").write_bytes(b"controlled test artifact")
    asyncio.run(install_local_profile(context, str(model_path)))
    provider = SimpleNamespace(prepare=prepare, loaded=True)
    registry = context.memory.provider_registry
    original_select = registry.select
    registry.select = lambda name: SimpleNamespace(
        chat_provider=provider, embedding_provider=provider
    )
    try:
        headers = login(client)
        response = client.post(
            "/api/control/v1/providers/local/prepare-local", headers=headers, json={"mode": "chat"}
        )
        assert response.status_code == 200
        assert response.json()["loaded"] is True
        assert prepared == [False]
        response = client.post(
            "/api/control/v1/providers/local/prepare-local",
            headers=headers,
            json={"mode": "chat", "allow_download": True},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "step_up_required"
        assert prepared == [False]
    finally:
        registry.select = original_select


def test_cli_index_status_and_activation_share_service_contract(controls, monkeypatch) -> None:
    import pzp_ai_bot.embedding_cli as cli

    context, indexes, _ = controls

    async def build(settings):
        return context

    monkeypatch.setattr(cli, "build_context", build)
    monkeypatch.setattr(cli, "get_settings", lambda: context.settings)
    result = CliRunner().invoke(app, ["embedding-index", "status", "--scope", "all"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["jobs"][0]["id"] == 7
    result = CliRunner().invoke(
        app, ["embedding-index", "activate", "7", "--version", "3", "--confirmation", "ACTIVATE 8"]
    )
    assert result.exit_code != 0
    assert [name for name, _ in indexes.calls] == ["status"]


def test_cli_errors_do_not_echo_unrecognized_prefixed_exception_text(controls, monkeypatch):
    import pzp_ai_bot.embedding_cli as cli

    context, indexes, _ = controls

    async def build(settings):
        return context

    async def broken_status(scope):
        raise ValueError("embedding_privateSecretKey")

    monkeypatch.setattr(cli, "build_context", build)
    monkeypatch.setattr(cli, "get_settings", lambda: context.settings)
    indexes.status = broken_status
    result = CliRunner().invoke(app, ["embedding-index", "status"])
    assert result.exit_code != 0
    assert "privateSecretKey" not in result.output
    assert "embedding_request_invalid" in result.output


@pytest.mark.asyncio
async def test_telegram_index_command_denies_chat_admin_and_bounds_owner_work(context) -> None:
    from pzp_ai_bot.telegram.routers.embedding_indexes import register_embedding_index_handlers
    from pzp_ai_bot.telegram.routers.shared import RouterGuards

    indexes = IndexServiceDouble()
    context.memory.embedding_indexes = indexes
    session = RecordingTelegramSession(chat_admin_ids={222})
    bot = Bot("123:fake", session=session)
    router = Router()

    async def require_owner(message):
        if message.from_user.id == 111:
            return True
        from pzp_ai_bot.telegram.safe_api import safe_answer

        await safe_answer(message, "Only bot admins can use embedding indexes")
        return False

    async def allow_chat_admin(message):
        return True

    async def allow_scope_admin(message, scope):
        return True

    guards = RouterGuards(allow_chat_admin, require_owner, allow_scope_admin)
    register_embedding_index_handlers(router, context, guards)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await feed(dispatcher, bot, message_update("/embedding_index run id=7 batches=2", user_id=222))
    assert indexes.calls == []
    await feed(dispatcher, bot, message_update("/embedding_index run id=7 batches=11", user_id=111))
    assert indexes.calls == []
    await feed(dispatcher, bot, message_update("/embedding_index run id=7 batches=2", user_id=111))
    assert indexes.calls == [("run", (7, 2))]
    assert all(len(text) <= 3900 for text in session.sent_texts())
    await bot.session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("file_size", [None, 600_000_000])
async def test_local_download_fails_closed_before_fetch_for_unknown_or_excessive_size(
    context, monkeypatch, file_size
) -> None:
    from pzp_ai_bot.memory import local_preparation

    downloads: list[dict[str, Any]] = []
    hub = SimpleNamespace(
        HfApi=lambda: SimpleNamespace(
            model_info=lambda **kwargs: SimpleNamespace(
                sha="a" * 40,
                siblings=[SimpleNamespace(rfilename="model.safetensors", size=file_size)],
            )
        ),
        snapshot_download=lambda **kwargs: downloads.append(kwargs),
    )
    monkeypatch.setattr(local_preparation, "import_local_dependency", lambda name: hub)
    await install_local_profile(context, options={"local_files_only": False})
    with pytest.raises(ValueError, match=r"local_download_(metadata_unavailable|too_large)"):
        await local_preparation.prepare_local_model(
            context, "local", mode="chat", allow_download=True
        )
    assert downloads == []


@pytest.mark.asyncio
async def test_local_download_is_pinned_allowlisted_then_loaded_offline(
    context, monkeypatch, tmp_path
) -> None:
    from pzp_ai_bot.memory import local_preparation

    downloads: list[dict[str, Any]] = []
    prepared: list[dict[str, Any]] = []
    snapshot = tmp_path / "models" / "cache" / "snapshots" / ("a" * 40)
    siblings = [
        SimpleNamespace(rfilename=name, size=size)
        for name, size in [
            ("model.safetensors", 100),
            ("tokenizer.json", 20),
            ("config.json", 10),
            ("model.bin", 999),
            ("custom_code.py", 999),
        ]
    ]

    def download(**kwargs):
        downloads.append(kwargs)
        return str(snapshot)

    async def prepare(**kwargs):
        prepared.append(kwargs)

    hub = SimpleNamespace(
        HfApi=lambda: SimpleNamespace(
            model_info=lambda **kwargs: SimpleNamespace(sha="a" * 40, siblings=siblings)
        ),
        snapshot_download=download,
    )
    monkeypatch.setattr(local_preparation, "import_local_dependency", lambda name: hub)
    provider = SimpleNamespace(prepare=prepare, loaded=True)
    secret_service = await install_local_profile(context, options={"local_files_only": False})
    registry = context.memory.provider_registry
    registry.select = lambda name: SimpleNamespace(
        chat_provider=provider, embedding_provider=provider
    )
    response = await local_preparation.prepare_local_model(
        context, "local", mode="chat", allow_download=True, actor_user_id=111
    )
    assert response["loaded"] is True
    assert response["resolved_revision"] == "a" * 40
    assert (await secret_service.get_profile("local")).local_chat_options["revision"] == "a" * 40
    assert downloads[0]["revision"] == "a" * 40
    assert downloads[0]["allow_patterns"] == ["config.json", "model.safetensors", "tokenizer.json"]
    assert prepared == [
        {"allow_download": False, "model_path": str(snapshot), "tokenizer_path": str(snapshot)}
    ]


@pytest.mark.parametrize("mode", ["chat", "embedding"])
def test_owner_profile_probe_is_synthetic_and_persists_safe_telemetry(controls, mode):
    context, _, client = controls
    headers = login(client)
    response = client.post(
        "/api/control/v1/providers/default/test", headers=headers, json={"mode": mode}
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "mode": mode,
        "detail": "Provider check completed successfully.",
    }

    async def inspect():
        async with session_scope(context.session_factory) as session:
            usages = list((await session.scalars(select(AIUsage))).all())
            assert len(usages) == 1
            assert usages[0].operation == f"provider_profile_test_{mode}"
            assert "synthetic" not in json.dumps(usages[0].metadata_json)

    asyncio.run(inspect())


def test_profile_probe_failure_never_returns_output_or_exception_text(controls):
    context, _, client = controls
    headers = login(client)
    selected = context.memory.provider_registry.select("default")

    async def broken(*args, **kwargs):
        raise RuntimeError("secret key and private provider content")

    selected.chat_provider.generate = broken
    response = client.post(
        "/api/control/v1/providers/default/test", headers=headers, json={"mode": "chat"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "mode": "chat",
        "detail": "Provider check failed. Check configuration, credentials and local preparation.",
    }
    assert "secret" not in response.text


def test_real_index_service_lifecycle_through_api(tmp_path):
    context, api = build_test_app(tmp_path, ai_provider="fake")
    with TestClient(api, base_url=ORIGIN) as client:
        headers = login(client)
        response = client.post(
            f"{ROOT}/jobs", headers=headers, json={"scope": "-1001", "provider_profile": "default"}
        )
        assert response.status_code == 200, response.text
        job = response.json()
        response = client.post(
            f"{ROOT}/jobs/{job['id']}/run", headers=headers, json={"max_batches": 1}
        )
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["status"] == "ready"
        status = client.get(ROOT, params={"scope": "-1001"})
        assert status.status_code == 200
        assert status.json()["spaces"][0]["dimension"] > 0
        asyncio.run(
            context.control_plane.sessions.mark_step_up(
                client.cookies["pzp_control_session"],
                now=datetime.now(UTC),
                action="embedding:activate",
            )
        )
        response = client.post(
            f"{ROOT}/jobs/{job['id']}/activate",
            headers=headers,
            json={"version": job["version"], "confirmation": f"ACTIVATE {job['id']}"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "activated"
    asyncio.run(context.memory.provider_registry.aclose())
    asyncio.run(context.session_factory.kw["bind"].dispose())


@pytest.mark.asyncio
async def test_cached_local_preparation_resolves_and_pins_without_metadata_network(
    context, monkeypatch
):
    from pzp_ai_bot.memory import local_preparation

    service = await install_local_profile(context)
    calls: list[dict[str, Any]] = []
    snapshot = context.settings.local_default_cache_dir / "snapshots" / ("b" * 40)

    def cached_snapshot(**kwargs):
        assert kwargs["local_files_only"] is True
        calls.append(kwargs)
        return str(snapshot)

    async def prepare(**kwargs):
        assert kwargs == {
            "allow_download": False,
            "model_path": str(snapshot),
            "tokenizer_path": str(snapshot),
        }

    hub = SimpleNamespace(snapshot_download=cached_snapshot)
    monkeypatch.setattr(local_preparation, "import_local_dependency", lambda name: hub)
    provider = SimpleNamespace(prepare=prepare, loaded=True)
    context.memory.provider_registry.select = lambda name: SimpleNamespace(chat_provider=provider)
    response = await local_preparation.prepare_local_model(context, "local", mode="chat")
    assert response["resolved_revision"] == "b" * 40
    assert response["downloaded"] is False
    assert response["download_bytes"] == 0
    assert len(calls) == 1
    assert (await service.get_profile("local")).local_chat_options["revision"] == "b" * 40


def test_cli_local_prepare_pins_keyless_environment_profile_and_audits_local_operator(
    test_settings, monkeypatch
):
    import pzp_ai_bot.embedding_cli as cli

    model = test_settings.memory_dir.parent / "models" / "fixture"
    model.mkdir(parents=True)
    (model / "model.safetensors").write_bytes(b"controlled fixture weights")
    (model / "config.json").write_text("{}", encoding="utf-8")
    test_settings.ai_provider_profiles = json.dumps(
        {
            "local": {
                "provider": "local",
                "chat_capable": True,
                "embedding_capable": False,
                "chat_model": str(model),
            }
        }
    )
    original_build = cli.build_context
    loaded: list[dict[str, Any]] = []

    async def prepare(**kwargs):
        loaded.append(kwargs)

    async def build(settings):
        built = await original_build(settings)
        original_select = built.memory.provider_registry.select
        provider = SimpleNamespace(prepare=prepare, loaded=True)
        monkeypatch.setattr(
            built.memory.provider_registry,
            "select",
            lambda name: (
                SimpleNamespace(chat_provider=provider)
                if name == "local"
                else original_select(name)
            ),
        )
        return built

    monkeypatch.setattr(cli, "get_settings", lambda: test_settings)
    monkeypatch.setattr(cli, "build_context", build)
    assert test_settings.control_plane_enabled is False
    assert not test_settings.control_plane_master_keys
    response = CliRunner().invoke(
        app, ["local-model", "prepare", "--profile", "local", "--mode", "chat"]
    )
    assert response.exit_code == 0, response.output
    result = json.loads(response.stdout)
    assert result["loaded"] is True
    assert result["downloaded"] is False
    assert result["resolved_revision"].startswith("sha256:")
    assert loaded == [
        {"allow_download": False, "model_path": str(model), "tokenizer_path": str(model)}
    ]

    async def inspect():
        from pzp_ai_bot.db.models import AIProviderProfile, BotEvent
        from pzp_ai_bot.db.session import create_session_factory

        sessions = create_session_factory(test_settings)
        async with session_scope(sessions) as session:
            profile = await session.scalar(
                select(AIProviderProfile).where(AIProviderProfile.name == "local")
            )
            assert profile is not None
            assert profile.local_chat_options["revision"] == result["resolved_revision"]
            event = await session.scalar(
                select(BotEvent).where(BotEvent.event_type == "local_model.prepared")
            )
            assert event is not None
            assert event.user_id is None
            assert event.payload["actor_kind"] == "local_operator"
        await sessions.kw["bind"].dispose()

    asyncio.run(inspect())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["chat", "embedding"])
async def test_changed_local_directory_digest_fails_before_dependency_or_weight_loading(
    tmp_path, monkeypatch, mode
):
    from pzp_ai_bot.ai import local, local_chat, local_runtime
    from pzp_ai_bot.local_models import LocalModelOptions

    (tmp_path / "config.json").write_bytes(b"{}")
    (tmp_path / "model.safetensors").write_bytes(b"initial weights")
    expected = hashlib.sha256(
        b"config.json\0"
        + hashlib.sha256(b"{}").digest()
        + b"model.safetensors\0"
        + hashlib.sha256(b"initial weights").digest()
    ).hexdigest()
    (tmp_path / "model.safetensors").write_bytes(b"changed weights")
    adapter = local_chat if mode == "chat" else local
    dependency_loads: list[str] = []
    worker_threads: list[int] = []
    main_thread = threading.get_ident()
    original_digest = local_runtime.local_directory_revision

    def digest_on_worker(path):
        worker_threads.append(threading.get_ident())
        return original_digest(path)

    def forbidden_dependency(name):
        dependency_loads.append(name)
        raise AssertionError("must validate provenance before loading model dependencies")

    monkeypatch.setattr(adapter, "import_local_dependency", forbidden_dependency)
    monkeypatch.setattr(local_runtime, "local_directory_revision", digest_on_worker)
    options = LocalModelOptions(revision=f"sha256:{expected}")
    provider_type = (
        local_chat.LocalTransformersChatProvider
        if mode == "chat"
        else local.LocalSentenceTransformerEmbeddingProvider
    )
    provider = provider_type(str(tmp_path), options=options)
    try:
        with pytest.raises(ValueError, match="local_artifact_revision_changed"):
            await provider.prepare(model_path=str(tmp_path))
        assert dependency_loads == []
        assert worker_threads and all(thread != main_thread for thread in worker_threads)
        assert provider.loaded is False
    finally:
        await provider.close()


@pytest.mark.parametrize(
    ("action", "offset"), [("profile:delete", 0), ("embedding:activate", 1000)]
)
def test_index_activation_rejects_wrong_action_and_expired_verification(controls, action, offset):
    context, indexes, client = controls
    headers = login(client)
    asyncio.run(
        context.control_plane.sessions.mark_step_up(
            client.cookies["pzp_control_session"],
            now=datetime.now(UTC) - timedelta(seconds=offset),
            action=action,
        )
    )
    response = client.post(
        f"{ROOT}/jobs/7/activate",
        headers=headers,
        json={"version": 3, "confirmation": "ACTIVATE 7"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "step_up_required"
    assert indexes.calls == []


def test_real_index_gets_do_not_construct_providers_or_execute_model_work(tmp_path, monkeypatch):
    context, api = build_test_app(tmp_path, ai_provider="fake")

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("metadata reads cannot construct or call model providers")

    monkeypatch.setattr(context.memory.provider_registry, "select", forbidden_provider)
    monkeypatch.setattr(
        context.memory._provider_operations, "generate_embeddings", forbidden_provider
    )
    with TestClient(api, base_url=ORIGIN) as client:
        login(client)
        assert client.get(ROOT).status_code == 200
        assert (
            client.get(f"{ROOT}/preview", params={"provider_profile": "default"}).status_code == 200
        )
    asyncio.run(context.session_factory.kw["bind"].dispose())
