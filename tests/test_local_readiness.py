from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from chatterlore.app_context import AppContext, build_context
from chatterlore.control_plane.app import create_control_plane_app
from chatterlore.control_plane.insights import ControlPlaneInsightsService
from chatterlore.control_plane.operations import ControlPlaneOperationsService
from chatterlore.control_plane.schemas import ProviderProfileCreateRequest
from chatterlore.control_plane.secrets.service import ProviderSecretService
from chatterlore.db.session import upgrade_schema
from test_control_plane_api import ORIGIN, FakeOIDCClient, settings_for, signed_init_data


async def local_operations(context: AppContext, *, mode: str, server: bool = False):
    service = ProviderSecretService(
        context.settings, context.session_factory, context.memory.provider_registry
    )
    await service.create_profile(
        ProviderProfileCreateRequest(
            name="local-ready",
            provider_kind="openai_compatible" if server else "local",
            local=True,
            auth_mode="none",
            base_url="http://localhost:11434/v1",
            chat_model="owner/tiny-chat" if mode in {"chat", "both"} else "",
            embedding_model="owner/tiny-embedding" if mode in {"embedding", "both"} else "",
            chat_capable=mode in {"chat", "both"},
            embedding_capable=mode in {"embedding", "both"},
            local_chat_options={"local_files_only": False},
            local_embedding_options={"local_files_only": False},
        ).model_dump(),
        actor_user_id=111,
        now=datetime.now(UTC),
    )
    operations = ControlPlaneOperationsService(
        context.settings,
        context.session_factory,
        memory=context.memory,
        secrets=service,
        insights=ControlPlaneInsightsService(
            context.settings, context.session_factory, admins=context.admins, secrets=service
        ),
    )
    return operations, service


@pytest.fixture
def prepared_runtime(context: AppContext, monkeypatch: pytest.MonkeyPatch) -> str:
    from chatterlore.memory import local_preparation

    monkeypatch.setattr(
        "chatterlore.control_plane.operations.importlib.util.find_spec", lambda name: object()
    )
    revision = "b" * 40
    snapshot = context.settings.local_default_cache_dir / "snapshots" / revision
    hub = SimpleNamespace(
        HfApi=lambda: SimpleNamespace(
            model_info=lambda **kwargs: SimpleNamespace(
                sha=revision,
                siblings=[
                    SimpleNamespace(rfilename=name, size=size)
                    for name, size in [("model.safetensors", 100), ("config.json", 10)]
                ],
            )
        ),
        snapshot_download=lambda **kwargs: str(snapshot),
    )
    monkeypatch.setattr(local_preparation, "import_local_dependency", lambda name: hub)

    class LocalBackend:
        name = "local"
        dimensions = 16

        def __init__(self, model: str, **kwargs) -> None:
            self.model = model
            self.embedding_model = model
            self.loaded = False

        async def prepare(self, **kwargs) -> None:
            assert kwargs["allow_download"] is False
            assert kwargs["model_path"] == str(snapshot)
            self.loaded = True

        async def aclose(self) -> None:
            self.loaded = False

    monkeypatch.setattr("chatterlore.ai.local.LocalTransformersChatProvider", LocalBackend)
    monkeypatch.setattr(
        "chatterlore.ai.local.LocalSentenceTransformerEmbeddingProvider", LocalBackend
    )
    return revision


@pytest.mark.parametrize("mode", ["chat", "embedding"])
async def test_local_readiness_observes_only_dependencies_for_enabled_capabilities(
    context: AppContext, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations, _ = await local_operations(context, mode=mode)
    available = {"torch", "transformers"} if mode == "chat" else {"sentence_transformers"}
    monkeypatch.setattr(
        "chatterlore.control_plane.operations.importlib.util.find_spec",
        lambda name: object() if name in available else None,
    )

    def no_inference(*args, **kwargs):
        raise AssertionError("Observational readiness must not select or load a provider")

    monkeypatch.setattr(context.memory.provider_registry, "select", no_inference)
    result = await operations.run_check(
        "local-model-readiness", profile_name="local-ready", actor_user_id=111
    )

    assert result["ok"] is True
    assert result["details"]["dependency_available"] is True
    assert result["details"]["model_loaded"] is False
    assert result["details"]["download_attempted"] is False


@pytest.mark.parametrize("allow_download", [False, True])
async def test_local_server_readiness_validates_configuration_without_network_or_download(
    context: AppContext, allow_download: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations, _ = await local_operations(context, mode="chat", server=True)

    def no_network(*args, **kwargs):
        raise AssertionError("Local server readiness must not invoke inference or downloads")

    monkeypatch.setattr(context.memory.provider_registry, "select", no_network)
    result = await operations.run_check(
        "local-model-readiness",
        profile_name="local-ready",
        actor_user_id=111,
        allow_download=allow_download,
    )

    assert result["ok"] is True
    assert result["result"] == "configured"
    assert result["details"]["server_configured"] is True
    assert result["details"]["download_attempted"] is False


async def test_observational_readiness_keeps_model_path_allowlist(
    context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations, _ = await local_operations(context, mode="chat")
    monkeypatch.setattr(
        context.settings,
        "ai_provider_profiles",
        json.dumps(
            {
                "outside": {
                    "provider": "local",
                    "chat_model": str(tmp_path / "outside-model"),
                    "embedding_capable": False,
                }
            }
        ),
    )
    with pytest.raises(ValueError, match="deployment allowlist"):
        await operations.run_check(
            "local-model-readiness", profile_name="outside", actor_user_id=111
        )


@pytest.mark.parametrize("mode", ["chat", "embedding"])
async def test_local_readiness_explicit_download_uses_bounded_preparation_without_inference(
    context: AppContext, mode: str, prepared_runtime: str
) -> None:
    operations, service = await local_operations(context, mode=mode)
    result = await operations.run_check(
        "local-model-readiness", profile_name="local-ready", actor_user_id=111, allow_download=True
    )

    assert result["ok"] is True
    assert result["details"]["model_loaded"] is True
    assert result["details"]["download_attempted"] is True
    assert (await service.get_profile("local-ready")).as_dict()[f"local_{mode}_options"][
        "revision"
    ] == prepared_runtime


@pytest.mark.parametrize("capacity", [1, 2])
async def test_dual_mode_preparation_reports_live_residency_without_requiring_both_resident(
    context: AppContext, capacity: int, prepared_runtime: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(context.settings, "local_max_loaded_models", capacity)
    operations, service = await local_operations(context, mode="both")

    result = await operations.run_check(
        "local-model-readiness", profile_name="local-ready", actor_user_id=111, allow_download=True
    )

    actual = context.memory.provider_registry.local_state("local-ready")
    assert result["ok"] is True
    assert result["result"] == "prepared"
    assert result["details"]["model_loaded"] is (capacity == 2)
    assert result["details"]["chat_model_loaded"] is actual["chat_model_loaded"]
    assert result["details"]["embedding_model_loaded"] is actual["embedding_model_loaded"]
    stored = await service.get_profile("local-ready")
    assert stored.local_chat_options["revision"] == prepared_runtime
    assert stored.local_embedding_options["revision"] == prepared_runtime


@pytest.mark.parametrize("server", [False, True], ids=["embedded", "local-server"])
def test_only_embedded_download_preparation_requires_step_up(
    tmp_path: Path, server: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path, ai_provider="fake")
    upgrade_schema(settings)
    context = asyncio.run(build_context(settings, ensure_schema=False))
    asyncio.run(local_operations(context, mode="chat", server=server))
    app = create_control_plane_app(context, oidc_client=FakeOIDCClient(user_id=111))
    monkeypatch.setattr(
        "chatterlore.control_plane.operations.importlib.util.find_spec", lambda name: object()
    )
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/control/v1/auth/mini-app",
            headers={"Origin": ORIGIN},
            json={"init_data": signed_init_data(111)},
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrf_token"]}
        observed = client.post(
            "/api/control/v1/operations/checks/local-model-readiness",
            headers=headers,
            json={"profile_name": "local-ready", "allow_download": False},
        )
        assert observed.status_code == 200
        requested = client.post(
            "/api/control/v1/operations/checks/local-model-readiness",
            headers=headers,
            json={"profile_name": "local-ready", "allow_download": True},
        )
        assert requested.status_code == (200 if server else 403)
        if not server:
            assert requested.json()["error"]["code"] == "step_up_required"
