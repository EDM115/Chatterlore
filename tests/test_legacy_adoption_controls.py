from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chatterlore.db.repos.control_plane import ControlPlaneAccessRepository
from chatterlore.db.session import session_scope
from chatterlore.main import app
from test_control_plane_api import ORIGIN, build_test_app
from test_embedding_index_controls import login
from test_legacy_adoption import legacy_manifest

PREVIEW = "/api/control/v1/embedding-indexes/legacy/preview"
ADOPT = "/api/control/v1/embedding-indexes/legacy/adopt"


@pytest.fixture
def adoption_controls(tmp_path):
    context, api = build_test_app(tmp_path, ai_provider="fake")
    manifest = asyncio.run(legacy_manifest(context))
    try:
        with TestClient(api, base_url=ORIGIN) as client:
            yield context, client, manifest
    finally:
        asyncio.run(context.session_factory.kw["bind"].dispose())


def test_owner_adoption_requires_csrf_and_its_own_fresh_step_up(adoption_controls):
    context, client, manifest = adoption_controls
    headers = login(client)
    assert client.post(PREVIEW, json={"manifest": manifest}).status_code == 403
    preview = client.post(PREVIEW, headers=headers, json={"manifest": manifest})
    assert preview.status_code == 200, preview.text
    assert "PRIVATE_" not in preview.text
    digest = preview.json()["preview_hash"]
    payload = {
        "manifest": manifest,
        "preview_hash": digest,
        "confirmation": f"ADOPT {digest}",
        "provenance_attested": True,
    }
    denied = client.post(ADOPT, headers=headers, json=payload)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "step_up_required"
    asyncio.run(
        context.control_plane.sessions.mark_step_up(
            client.cookies["chatterlore_control_session"],
            now=datetime.now(UTC),
            action="embedding:activate",
        )
    )
    assert client.post(ADOPT, headers=headers, json=payload).status_code == 403
    asyncio.run(
        context.control_plane.sessions.mark_step_up(
            client.cookies["chatterlore_control_session"],
            now=datetime.now(UTC),
            action="embedding:adopt",
        )
    )
    response = client.post(ADOPT, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["adopted"] == 2
    assert client.post(ADOPT, headers=headers, json=payload).status_code == 409


def test_session_exposes_exact_action_and_server_computed_step_up_expiry(adoption_controls):
    context, client, _ = adoption_controls
    login(client)
    initial = client.get("/api/control/v1/session").json()
    assert initial["step_up_action"] is None
    assert initial["step_up_expires_at"] is None
    verified_at = datetime.now(UTC)
    asyncio.run(
        context.control_plane.sessions.mark_step_up(
            client.cookies["chatterlore_control_session"], now=verified_at, action="embedding:adopt"
        )
    )
    current = client.get("/api/control/v1/session").json()
    assert current["step_up_action"] == "embedding:adopt"
    assert datetime.fromisoformat(current["step_up_expires_at"]) == verified_at + timedelta(
        seconds=context.settings.control_plane_step_up_max_age_seconds
    )


def test_real_delegated_admin_cannot_preview_or_adopt_legacy_vectors(adoption_controls):
    context, client, manifest = adoption_controls

    async def grant():
        async with session_scope(context.session_factory) as db:
            repo = ControlPlaneAccessRepository(db)
            await repo.upsert_managed_chat(
                chat_id=-100555,
                title="Scoped fixture",
                username=None,
                status="active",
                actor_user_id=111,
                now=datetime.now(UTC),
            )
            await repo.grant_chat_admin(
                chat_id=-100555, telegram_user_id=333, actor_user_id=111, now=datetime.now(UTC)
            )

    asyncio.run(grant())
    headers = login(client, 333)
    assert client.post(PREVIEW, headers=headers, json={"manifest": manifest}).status_code == 403
    assert (
        client.post(
            ADOPT,
            headers=headers,
            json={
                "manifest": manifest,
                "preview_hash": "a" * 64,
                "confirmation": "ADOPT " + "a" * 64,
                "provenance_attested": True,
            },
        ).status_code
        == 403
    )


def test_cli_adoption_manifest_preview_and_explicit_commit_are_service_backed(
    context, monkeypatch, tmp_path
):
    import chatterlore.embedding_cli as cli

    manifest = asyncio.run(legacy_manifest(context))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    async def build(settings):
        return context

    monkeypatch.setattr(cli, "build_context", build)
    monkeypatch.setattr(cli, "get_settings", lambda: context.settings)
    preview = CliRunner().invoke(app, ["embedding-index", "adopt-preview", "--manifest", str(path)])
    assert preview.exit_code == 0, preview.output
    digest = json.loads(preview.output)["preview_hash"]
    args = [
        "embedding-index",
        "adopt",
        "--manifest",
        str(path),
        "--preview-hash",
        digest,
        "--confirmation",
        f"ADOPT {digest}",
    ]
    denied = CliRunner().invoke(app, args)
    assert denied.exit_code != 0
    result = CliRunner().invoke(app, [*args, "--attest-provenance"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["adopted"] == 2


def test_cli_adoption_rejects_unbounded_or_invalid_documents_without_echoing_contents(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"PRIVATE_SECRET": "never echo me"}', encoding="utf-8")
    result = CliRunner().invoke(app, ["embedding-index", "adopt-preview", "--manifest", str(path)])
    assert result.exit_code != 0
    assert "PRIVATE_SECRET" not in result.output and "never echo me" not in result.output
    assert "embedding_adoption_manifest_invalid" in result.output
