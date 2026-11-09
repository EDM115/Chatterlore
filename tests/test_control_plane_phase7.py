from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine

from chatterlore.app_context import AppContext, build_context
from chatterlore.control_plane.app import _memory_export_stream, create_control_plane_app
from chatterlore.control_plane.insights import bounded_directory_size
from chatterlore.db.models import AIUsage
from chatterlore.db.repos.control_plane import ControlPlaneAccessRepository
from chatterlore.db.repos.events import EventRepository
from chatterlore.db.repos.knowledge import KnowledgeRepository
from chatterlore.db.repos.profiles import MemoryReviewRepository
from chatterlore.db.session import create_schema, session_scope, upgrade_schema
from chatterlore.memory.contracts import AIRequestOptions, IncomingMessage
from chatterlore.memory.scope import Scope
from test_control_plane_api import ORIGIN, FakeOIDCClient, settings_for, signed_init_data

NOW = datetime(2026, 8, 2, 9, 0, tzinfo=UTC)


@pytest.fixture(params=[False, True], ids=["without-local-extra", "with-local-extra"])
def local_dependency_available(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    available = bool(request.param)
    original_find_spec = importlib.util.find_spec

    def find_spec(name: str, package: str | None = None):
        if name in {"sentence_transformers", "torch", "transformers"}:
            return ModuleSpec(name, loader=None) if available else None
        return original_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    return available


def _build_phase7_app(tmp_path: Path, **overrides: object):
    values: dict[str, object] = {
        "allowed_chat_models": "fake-chat,gpt-6.1-terra",
        "allowed_embedding_models": (
            "fake-embedding,text-embedding-3-small,"
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        ),
        "allowed_reasoning_levels": "low,medium,high",
        "ai_provider_profiles": json.dumps(
            {
                "local": {
                    "provider": "local",
                    "chat_model": "fake-chat",
                    "chat_capable": False,
                    "embedding_model": (
                        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
                    ),
                }
            }
        ),
    }
    values.update(overrides)
    settings = settings_for(tmp_path, **values)
    upgrade_schema(settings)
    context = asyncio.run(build_context(settings, ensure_schema=False))
    app = create_control_plane_app(context, oidc_client=FakeOIDCClient(user_id=111))
    return context, app


async def _seed_managed_chat(context, chat_id: int = -1001) -> None:
    assert context.control_plane is not None
    await context.control_plane.management.enroll_chat(
        chat_id=chat_id, title="Phase 7 chat", username=None, actor_user_id=111, now=NOW
    )


def _login(client: TestClient, user_id: int = 111) -> dict[str, str]:
    response = client.post(
        "/api/control/v1/auth/mini-app",
        headers={"Origin": ORIGIN},
        json={"init_data": signed_init_data(user_id)},
    )
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


async def _measure_queries[T](
    context: AppContext, operation: Callable[[], Awaitable[T]]
) -> tuple[T, int]:
    engine = context.session_factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    query_count = 0

    def count_query(*_args: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    try:
        result = await operation()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_query)
    return result, query_count


def test_query_catalog_is_scope_authorized_capability_accurate_and_secret_free(
    tmp_path: Path, local_dependency_available: bool
) -> None:
    context, app = _build_phase7_app(tmp_path)

    async def seed() -> None:
        await _seed_managed_chat(context)
        assert context.control_plane is not None
        await context.control_plane.secrets.create_profile(
            {
                "name": "managed-openai",
                "provider_kind": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "chat_model": "gpt-6.1-terra",
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

    asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        response = client.get("/api/control/v1/query/catalog?scope=-1001")

        assert response.status_code == 200
        payload = response.json()
        assert payload["scope"] == "-1001:main"
        assert payload["chat_models"] == ["fake-chat", "gpt-6.1-terra"]
        assert payload["embedding_models"] == [
            "fake-embedding",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "text-embedding-3-small",
        ]
        assert payload["reasoning_levels"] == ["high", "low", "medium"]
        profiles = {item["name"]: item for item in payload["profiles"]}
        assert profiles["default"]["capabilities"] == ["chat", "embedding"]
        assert profiles["default"]["availability"] == "ready"
        assert profiles["local"]["capabilities"] == ["embedding"]
        assert profiles["local"]["local_state"] == {
            "dependency_available": local_dependency_available,
            "model_loaded": False,
            "chat_dependency_available": local_dependency_available,
            "embedding_dependency_available": local_dependency_available,
            "chat_model_loaded": False,
            "embedding_model_loaded": False,
            "server_configured": False,
        }
        assert profiles["local"]["availability"] == (
            "preparation_required" if local_dependency_available else "unavailable"
        )
        assert profiles["managed-openai"]["capabilities"] == ["chat", "embedding", "reasoning"]
        assert profiles["managed-openai"]["availability"] == "unavailable"
        assert profiles["managed-openai"]["pool"] == {"eligible": 0, "total": 0}
        serialized = json.dumps(payload).lower()
        assert '"api_key":' not in serialized
        assert "openai_api_key" not in serialized
        assert "credential_id" not in serialized
        assert "fingerprint" not in serialized
        assert "last_four" not in serialized


def test_query_catalog_rejects_an_unmanaged_scope(tmp_path: Path) -> None:
    _context, app = _build_phase7_app(tmp_path)

    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        response = client.get("/api/control/v1/query/catalog?scope=-9999")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "managed_chat_not_found"


def test_one_shot_query_profiles_split_retrieval_and_chat_while_legacy_shorthand_remains(
    tmp_path: Path,
) -> None:
    profiles = {
        name: {
            "provider": "fake",
            "chat_model": f"{name}-chat",
            "embedding_model": f"{name}-embedding",
        }
        for name in ("legacy", "retrieval", "conversation")
    }
    settings = settings_for(
        tmp_path,
        ai_provider="fake",
        ai_provider_profiles=json.dumps(profiles),
        allowed_chat_models=("fake-chat,legacy-chat,retrieval-chat,conversation-chat"),
        allowed_embedding_models=(
            "fake-embedding,legacy-embedding,retrieval-embedding,conversation-embedding"
        ),
    )
    asyncio.run(create_schema(settings))
    context = asyncio.run(build_context(settings, ensure_schema=False))
    scope = Scope(-1001, None)

    async def exercise() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        assert context.control_plane is not None
        await context.control_plane.management.enroll_chat(
            chat_id=-1001, title="Split providers", username=None, actor_user_id=111, now=NOW
        )
        await context.memory.remember(scope, "The deployment codename is kestrel.")
        for profile in ("legacy", "retrieval"):
            job = await context.memory.embedding_indexes.create_job(
                scope, profile, actor_user_id=111
            )
            ready = await context.memory.embedding_indexes.run_job(job["id"])
            assert ready["status"] == "ready"
        await context.memory.ask(
            scope,
            "What is the deployment codename?",
            ai_options=AIRequestOptions(
                provider_profile="legacy",
                chat_model="legacy-chat",
                embedding_model="legacy-embedding",
            ),
        )
        await context.memory.ask(
            scope,
            "What is the deployment codename?",
            ai_options=AIRequestOptions(
                retrieval_provider_profile="retrieval",
                chat_provider_profile="conversation",
                chat_model="conversation-chat",
                embedding_model="retrieval-embedding",
            ),
        )
        async with session_scope(context.session_factory) as session:
            rows = list(
                (
                    await session.scalars(
                        select(AIUsage)
                        .where(AIUsage.operation.in_(("embed_query", "ask")))
                        .order_by(AIUsage.id.asc())
                    )
                ).all()
            )
        assert len(rows) == 4
        legacy = {
            row.operation: {
                "model": row.model,
                "profile": (row.metadata_json or {})["provider_profile"],
            }
            for row in rows[:2]
        }
        split = {
            row.operation: {
                "model": row.model,
                "profile": (row.metadata_json or {})["provider_profile"],
            }
            for row in rows[2:]
        }
        return legacy, split

    legacy, split = asyncio.run(exercise())

    assert legacy == {
        "embed_query": {"model": "legacy-embedding", "profile": "legacy"},
        "ask": {"model": "legacy-chat", "profile": "legacy"},
    }
    assert split == {
        "embed_query": {"model": "retrieval-embedding", "profile": "retrieval"},
        "ask": {"model": "conversation-chat", "profile": "conversation"},
    }


def test_search_and_ask_api_return_safe_split_selection_and_source_metadata(tmp_path: Path) -> None:
    profiles = {
        "retrieval": {
            "provider": "fake",
            "chat_model": "retrieval-chat",
            "embedding_model": "retrieval-embedding",
        },
        "conversation": {
            "provider": "fake",
            "chat_model": "conversation-chat",
            "embedding_model": "conversation-embedding",
        },
        "local": {
            "provider": "local",
            "chat_model": "local-chat-unavailable",
            "chat_capable": False,
            "embedding_model": "local-embedding",
        },
    }
    settings = settings_for(
        tmp_path,
        ai_provider="fake",
        ai_provider_profiles=json.dumps(profiles),
        allowed_chat_models=("fake-chat,retrieval-chat,conversation-chat,local-chat-unavailable"),
        allowed_embedding_models=(
            "fake-embedding,retrieval-embedding,conversation-embedding,local-embedding"
        ),
        allowed_reasoning_levels="medium",
    )
    asyncio.run(create_schema(settings))
    context = asyncio.run(build_context(settings, ensure_schema=False))
    app = create_control_plane_app(context, oidc_client=FakeOIDCClient(user_id=111))

    async def seed() -> None:
        await _seed_managed_chat(context)
        await context.memory.remember(
            Scope(-1001, None), "The Phase 7 launch codename is kestrel.", source_message_id=77
        )
        job = await context.memory.embedding_indexes.create_job(
            Scope(-1001, None), "retrieval", actor_user_id=111
        )
        ready = await context.memory.embedding_indexes.run_job(job["id"])
        assert ready["status"] == "ready"

    asyncio.run(seed())
    common = {
        "scope": "-1001",
        "retrieval_provider_profile": "retrieval",
        "embedding_model": "retrieval-embedding",
        "top_k": 4,
    }

    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        searched = client.post(
            "/api/control/v1/query/search",
            headers=headers,
            json={**common, "query": "What is the Phase 7 launch codename?"},
        )
        asked = client.post(
            "/api/control/v1/query/ask",
            headers=headers,
            json={
                **common,
                "question": "What is the Phase 7 launch codename?",
                "chat_provider_profile": "conversation",
                "chat_model": "conversation-chat",
                "reasoning_level": "medium",
            },
        )
        local_chat = client.post(
            "/api/control/v1/query/ask",
            headers=headers,
            json={
                **common,
                "question": "What is the Phase 7 launch codename?",
                "chat_provider_profile": "local",
                "chat_model": "local-chat-unavailable",
                "reasoning_level": "medium",
            },
        )

    assert searched.status_code == 200
    search_payload = searched.json()
    assert search_payload["destination_scope"] == "-1001:main"
    assert search_payload["source_scopes"] == ["-1001:main"]
    assert search_payload["selection"] == {
        "retrieval_provider_profile": "retrieval",
        "embedding_model": "retrieval-embedding",
        "chat_provider_profile": None,
        "chat_model": None,
        "reasoning_level": None,
    }
    assert search_payload["elapsed_ms"] >= 0
    assert search_payload["results"][0]["content"] == "The Phase 7 launch codename is kestrel."
    assert search_payload["results"][0]["scope"] == "-1001:main"
    assert search_payload["results"][0]["source_message_ids"] == [77]

    assert asked.status_code == 200
    ask_payload = asked.json()
    assert ask_payload["insufficient_context"] is False
    assert "kestrel" in ask_payload["answer"].lower()
    assert ask_payload["selection"] == {
        "retrieval_provider_profile": "retrieval",
        "embedding_model": "retrieval-embedding",
        "chat_provider_profile": "conversation",
        "chat_model": "conversation-chat",
        "reasoning_level": "medium",
    }
    assert ask_payload["results"][0]["source_message_ids"] == [77]
    assert "prompt" not in json.dumps(ask_payload).lower()

    assert local_chat.status_code == 422
    assert local_chat.json()["error"]["code"] == "query_selection_invalid"


def test_ask_api_preserves_insufficient_context_without_calling_chat(tmp_path: Path) -> None:
    context, app = _build_phase7_app(tmp_path)
    asyncio.run(_seed_managed_chat(context))

    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        response = client.post(
            "/api/control/v1/query/ask",
            headers=headers,
            json={"scope": "-1001", "question": "What memory does not exist?", "top_k": 3},
        )

    assert response.status_code == 200
    assert response.json()["insufficient_context"] is True
    assert response.json()["results"] == []
    assert "I do not have enough scoped memory" in response.json()["answer"]

    async def operations() -> list[str]:
        async with session_scope(context.session_factory) as session:
            return list(await session.scalars(select(AIUsage.operation).order_by(AIUsage.id.asc())))

    assert asyncio.run(operations()) == ["embed_query"]


def test_ask_api_rejects_invalid_reasoning_before_any_provider_call(tmp_path: Path) -> None:
    context, app = _build_phase7_app(tmp_path, allowed_reasoning_levels="medium")
    asyncio.run(_seed_managed_chat(context))

    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        response = client.post(
            "/api/control/v1/query/ask",
            headers=headers,
            json={
                "scope": "-1001",
                "question": "This request must fail before retrieval.",
                "reasoning_level": "high",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "query_selection_invalid"

    async def usage_count() -> int:
        async with session_scope(context.session_factory) as session:
            return len(list(await session.scalars(select(AIUsage.id))))

    assert asyncio.run(usage_count()) == 0


def test_memory_items_are_keyset_paginated_and_raw_sources_are_explicit_audited_and_authorized(
    tmp_path: Path,
) -> None:
    context, app = _build_phase7_app(tmp_path)

    async def seed() -> tuple[list[int], int]:
        await _seed_managed_chat(context, -1001)
        await _seed_managed_chat(context, -1002)
        await context.memory.set_learning(Scope(-1001, None), True, updated_by=111)
        await context.memory.ingest(
            IncomingMessage(
                telegram_message_id=701,
                scope=Scope(-1001, None),
                user_id=444,
                username="source-user",
                text="RAW-SOURCE-BODY-ONLY-ON-EXPLICIT-ACCESS",
                created_at=NOW,
            )
        )
        item_ids = [
            await context.memory.remember(
                Scope(-1001, None), content, source_message_id=701 if index == 0 else None
            )
            for index, content in enumerate(
                ("First durable item.", "Second durable item.", "Third durable item.")
            )
        ]
        foreign_item_id = await context.memory.remember(Scope(-1002, None), "Foreign durable item.")
        async with session_scope(context.session_factory) as session:
            await ControlPlaneAccessRepository(session).grant_chat_admin(
                chat_id=-1002, telegram_user_id=333, actor_user_id=111, now=NOW
            )
        return item_ids, foreign_item_id

    item_ids, foreign_item_id = asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as owner:
        owner_headers = _login(owner)
        first_page = owner.get("/api/control/v1/memory/items?scope=-1001&limit=2")
        assert first_page.status_code == 200
        assert [item["id"] for item in first_page.json()["items"]] == item_ids[:2]
        assert first_page.json()["has_more"] is True
        assert first_page.json()["next_cursor"] == item_ids[1]
        assert "RAW-SOURCE-BODY" not in first_page.text

        second_page = owner.get(
            f"/api/control/v1/memory/items?scope=-1001&limit=2&cursor={item_ids[1]}"
        )
        assert [item["id"] for item in second_page.json()["items"]] == item_ids[2:]
        assert second_page.json()["has_more"] is False
        assert second_page.json()["next_cursor"] is None

        detail = owner.get(f"/api/control/v1/memory/items/{item_ids[0]}")
        assert detail.status_code == 200
        assert detail.json()["content"] == "First durable item."
        assert "RAW-SOURCE-BODY" not in detail.text

        sources = owner.post(
            f"/api/control/v1/memory/items/{item_ids[0]}/sources", headers=owner_headers
        )
        assert sources.status_code == 200
        assert sources.json() == {
            "item_id": item_ids[0],
            "scope": "-1001:main",
            "sources": [
                {
                    "telegram_message_id": 701,
                    "user_id": 444,
                    "username": "source-user",
                    "text": "RAW-SOURCE-BODY-ONLY-ON-EXPLICIT-ACCESS",
                    "created_at": NOW.isoformat().replace("+00:00", "Z"),
                    "message_permalink": None,
                }
            ],
        }

    with TestClient(app, base_url=ORIGIN) as delegated:
        _login(delegated, 333)
        denied = delegated.get(f"/api/control/v1/memory/items/{item_ids[0]}")
        allowed = delegated.get(f"/api/control/v1/memory/items/{foreign_item_id}")
        assert denied.status_code == 403
        assert allowed.status_code == 200

    async def raw_source_events() -> list[dict[str, object]]:
        events = await context.memory.audit_events(
            scope=Scope(-1001, None),
            event_type="control_plane.memory.raw_sources_accessed",
            limit=10,
        )
        return [event.payload for event in events]

    assert asyncio.run(raw_source_events()) == [
        {"knowledge_item_id": item_ids[0], "source_count": 1}
    ]


def test_memory_item_maintenance_and_review_pages_reuse_existing_scoped_workflows(
    tmp_path: Path,
) -> None:
    context, app = _build_phase7_app(tmp_path)

    async def seed() -> tuple[int, int, list[int]]:
        await _seed_managed_chat(context)
        refresh_id = await context.memory.remember(Scope(-1001, None), "Refresh this item.")
        delete_id = await context.memory.remember(Scope(-1001, None), "Delete this item.")
        async with session_scope(context.session_factory) as session:
            repository = MemoryReviewRepository(session)
            reviews = [
                await repository.add(
                    scope=Scope(-1001, None),
                    kind="summary",
                    content=f"Review proposal {index}.",
                    source_message_ids=[],
                )
                for index in range(1, 4)
            ]
        return refresh_id, delete_id, [review.id for review in reviews]

    refresh_id, delete_id, review_ids = asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        refreshed = client.post(
            f"/api/control/v1/memory/items/{refresh_id}/refresh",
            headers=headers,
            json={"mode": "embed"},
        )
        deleted = client.delete(f"/api/control/v1/memory/items/{delete_id}", headers=headers)
        first_reviews = client.get("/api/control/v1/memory/reviews?scope=-1001&limit=2")
        second_reviews = client.get(
            f"/api/control/v1/memory/reviews?scope=-1001&limit=2&cursor={review_ids[1]}"
        )
        approved = client.post(
            f"/api/control/v1/memory/reviews/{review_ids[0]}/approve", headers=headers
        )
        rejected = client.post(
            f"/api/control/v1/memory/reviews/{review_ids[1]}/reject", headers=headers
        )

    assert refreshed.status_code == 200
    assert refreshed.json() == {
        "id": refresh_id,
        "scope": "-1001:main",
        "kind": "manual_note",
        "mode": "embed",
        "summarized": False,
        "embeddings_refreshed": 1,
    }
    assert deleted.status_code == 200
    assert deleted.json()["id"] == delete_id
    assert asyncio.run(context.memory.memory_status(Scope(-1001, None))).knowledge_items == 2

    assert first_reviews.status_code == 200
    assert [item["id"] for item in first_reviews.json()["items"]] == review_ids[:2]
    assert first_reviews.json()["next_cursor"] == review_ids[1]
    assert first_reviews.json()["has_more"] is True
    assert [item["id"] for item in second_reviews.json()["items"]] == review_ids[2:]
    assert second_reviews.json()["next_cursor"] is None
    assert second_reviews.json()["has_more"] is False

    assert approved.status_code == 200
    assert approved.json()["review_id"] == review_ids[0]
    assert approved.json()["status"] == "approved"
    assert approved.json()["knowledge_item_id"] > 0
    assert rejected.status_code == 200
    assert rejected.json() == {
        "review_id": review_ids[1],
        "status": "rejected",
        "knowledge_item_id": None,
    }

    remaining = asyncio.run(context.memory.pending_reviews(Scope(-1001, None)))
    assert [review.id for review in remaining] == [review_ids[2]]


def test_memory_exports_stream_existing_markdown_and_jsonl_with_sensitive_download_headers(
    tmp_path: Path,
) -> None:
    context, app = _build_phase7_app(tmp_path)

    async def seed() -> None:
        await _seed_managed_chat(context)
        await context.memory.remember(
            Scope(-1001, None), "Stream this durable memory.", source_message_id=801
        )

    asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        markdown = client.get("/api/control/v1/memory/export?scope=-1001&format=md")
        jsonl = client.get("/api/control/v1/memory/export?scope=-1001&format=jsonl")

    assert markdown.status_code == 200
    assert markdown.headers["content-type"] == "text/markdown; charset=utf-8"
    assert markdown.headers["content-disposition"] == (
        'attachment; filename="memory_-1001_main.md"'
    )
    assert markdown.headers["cache-control"] == "no-store, private"
    assert markdown.headers["pragma"] == "no-cache"
    assert markdown.headers["x-content-type-options"] == "nosniff"
    assert "content-length" not in markdown.headers
    assert "Stream this durable memory." in markdown.text

    assert jsonl.status_code == 200
    assert jsonl.headers["content-type"] == "application/x-ndjson"
    assert jsonl.headers["content-disposition"] == (
        'attachment; filename="memory_-1001_main.jsonl"'
    )
    assert json.loads(jsonl.text)["content"] == "Stream this durable memory."


def test_memory_export_stream_closes_the_underlying_generator_on_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _app = _build_phase7_app(tmp_path)
    closed = False

    async def fake_export(_scope: Scope | None, *, fmt: str):
        nonlocal closed
        try:
            yield SimpleNamespace(text=f"first-{fmt}")
            yield SimpleNamespace(text="second")
        finally:
            closed = True

    monkeypatch.setattr(context.memory, "export_memory_chunks", fake_export)

    async def cancel_after_first_chunk() -> bytes:
        stream = _memory_export_stream(context, Scope(-1001, None), "md")
        first = await anext(stream)
        await stream.aclose()
        return first

    assert asyncio.run(cancel_after_first_chunk()) == b"first-md"
    assert closed is True


def test_stats_overview_and_timeseries_are_bounded_scoped_and_provider_side_effect_free(
    tmp_path: Path, local_dependency_available: bool
) -> None:
    context, app = _build_phase7_app(tmp_path)
    stats_now = datetime.now(UTC)

    async def seed() -> int:
        await _seed_managed_chat(context)
        await context.memory.set_learning(Scope(-1001, None), True, updated_by=111)
        await context.memory.ingest(
            IncomingMessage(
                telegram_message_id=901,
                scope=Scope(-1001, None),
                user_id=111,
                username="owner",
                text="A learned message for statistics.",
                created_at=stats_now,
            )
        )
        await context.memory.remember(Scope(-1001, None), "A durable statistics item.")
        async with session_scope(context.session_factory) as session:
            return len(list(await session.scalars(select(AIUsage.id))))

    usage_before = asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as client:
        _login(client)
        overview = client.get("/api/control/v1/stats/overview?scope=-1001")
        timeseries = client.get("/api/control/v1/stats/timeseries?scope=-1001&days=7")

    assert overview.status_code == 200
    payload = overview.json()
    assert payload["scope"] == "-1001:main"
    assert payload["memory"] == {
        "messages": 1,
        "learned": 1,
        "skipped": 0,
        "knowledge_items": 1,
        "embeddings": 2,
        "pending_reviews": 0,
        "audit_events": payload["memory"]["audit_events"],
    }
    assert payload["ai_usage"]["total"] == usage_before
    assert payload["runtime"]["uptime_seconds"] >= 0
    assert payload["runtime"]["schema_revision"] == "0019_embedding_spaces"
    assert payload["runtime"]["scheduler"] == []
    assert payload["storage"]["database_bytes"] > 0
    assert payload["storage"]["memory_bytes"] > 0
    assert payload["storage"]["memory_scan_truncated"] is False
    assert payload["administration"]["bot_admins"] == 1
    assert payload["administration"]["managed_chats"] == {"active": 1}
    assert payload["providers"]["profiles"] == 2
    assert payload["providers"]["local_dependency_available"] is local_dependency_available
    assert payload["providers"]["local_models_loaded"] == 0

    assert timeseries.status_code == 200
    series = timeseries.json()
    assert series["scope"] == "-1001:main"
    assert series["days"] == 7
    point = next(item for item in series["points"] if item["date"] == stats_now.date().isoformat())
    assert point["messages"] == 1
    assert point["learned"] == 1
    assert point["knowledge_items"] == 1

    async def usage_after() -> int:
        async with session_scope(context.session_factory) as session:
            return len(list(await session.scalars(select(AIUsage.id))))

    assert asyncio.run(usage_after()) == usage_before


def test_bounded_directory_size_stops_scandir_lazily_at_the_entry_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeEntry:
        path = str(tmp_path / "fake")

        def is_symlink(self) -> bool:
            return False

        def is_dir(self, *, follow_symlinks: bool) -> bool:
            return False

        def is_file(self, *, follow_symlinks: bool) -> bool:
            return True

        def stat(self, *, follow_symlinks: bool) -> SimpleNamespace:
            return SimpleNamespace(st_size=1)

    class FakeScandir:
        def __init__(self) -> None:
            self.next_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def __iter__(self):
            return self

        def __next__(self) -> FakeEntry:
            self.next_calls += 1
            if self.next_calls > 3:
                raise AssertionError("scandir was consumed past the configured entry cap")
            return FakeEntry()

    scan = FakeScandir()
    monkeypatch.setattr("chatterlore.control_plane.insights.os.scandir", lambda _path: scan)

    assert bounded_directory_size(tmp_path, max_entries=2) == (2, 2, True)
    assert scan.next_calls == 3


def test_audit_usage_and_manual_operations_are_paginated_safe_and_explicit(
    tmp_path: Path, local_dependency_available: bool
) -> None:
    context, app = _build_phase7_app(tmp_path)

    async def seed() -> int:
        await _seed_managed_chat(context)
        await context.memory.remember(Scope(-1001, None), "Usage event seed.")
        async with session_scope(context.session_factory) as session:
            repository = EventRepository(session)
            for index in range(3):
                await repository.add(
                    event_type="phase7_safe_event",
                    scope=Scope(-1001, None),
                    user_id=111,
                    payload={
                        "sequence": index,
                        "prompt": "PRIVATE-PROMPT-MUST-NOT-LEAVE",
                        "api_key": "PRIVATE-KEY-MUST-NOT-LEAVE",
                        "nested": {"safe": "visible", "token": "PRIVATE-TOKEN"},
                    },
                )
            return len(list(await session.scalars(select(AIUsage.id))))

    usage_before = asyncio.run(seed())

    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        first_events = client.get(
            "/api/control/v1/audit/events?scope=-1001&event_type=phase7_safe_event&limit=2"
        )
        event_cursor = first_events.json()["next_cursor"]
        second_events = client.get(
            "/api/control/v1/audit/events"
            f"?scope=-1001&event_type=phase7_safe_event&limit=2&cursor={event_cursor}"
        )
        usage = client.get("/api/control/v1/audit/ai-usage?scope=-1001&limit=10")
        health = client.get("/api/control/v1/operations/health")
        integrity = client.post(
            "/api/control/v1/operations/checks/db-integrity", headers=headers, json={}
        )
        projection = client.post(
            "/api/control/v1/operations/checks/projection-status", headers=headers, json={}
        )
        provider = client.post(
            "/api/control/v1/operations/checks/provider-profile",
            headers=headers,
            json={"profile_name": "default", "mode": "chat"},
        )
        local = client.post(
            "/api/control/v1/operations/checks/local-model-readiness",
            headers=headers,
            json={"profile_name": "local", "allow_download": False},
        )

    assert first_events.status_code == 200
    assert [item["payload"]["sequence"] for item in first_events.json()["items"]] == [2, 1]
    assert first_events.json()["has_more"] is True
    assert event_cursor == first_events.json()["items"][-1]["id"]
    assert [item["payload"]["sequence"] for item in second_events.json()["items"]] == [0]
    event_text = first_events.text + second_events.text
    assert "PRIVATE-" not in event_text
    assert "visible" in event_text

    assert usage.status_code == 200
    assert "embed_knowledge" in {item["operation"] for item in usage.json()["items"]}
    assert "metadata" not in usage.text.lower()
    assert "PRIVATE-" not in usage.text

    assert health.status_code == 200
    assert health.json()["checks"] == [
        "db-integrity",
        "local-model-readiness",
        "projection-status",
        "provider-profile",
    ]
    assert health.json()["provider_calls_performed"] is False

    assert integrity.status_code == 200
    assert integrity.json()["ok"] is True
    assert integrity.json()["result"] == "ok"
    assert projection.status_code == 200
    assert projection.json()["ok"] is True
    assert projection.json()["details"]["main_projection_exists"] is True

    assert provider.status_code == 200
    assert provider.json()["ok"] is True
    assert provider.json()["details"]["profile_name"] == "default"
    assert provider.json()["details"]["mode"] == "chat"
    assert "content" not in provider.text.lower()
    assert local.status_code == 200
    assert local.json()["ok"] is local_dependency_available
    assert local.json()["result"] == (
        "ready" if local_dependency_available else "dependency_unavailable"
    )
    assert local.json()["details"]["dependency_available"] is local_dependency_available
    assert local.json()["details"]["download_attempted"] is False

    async def usage_after() -> int:
        async with session_scope(context.session_factory) as session:
            return len(list(await session.scalars(select(AIUsage.id))))

    assert asyncio.run(usage_after()) == usage_before + 1


def test_projection_status_check_is_observational_and_does_not_repair_files(tmp_path: Path) -> None:
    context, app = _build_phase7_app(tmp_path)
    context.memory.markdown.main_path.unlink()

    with TestClient(app, base_url=ORIGIN) as client:
        headers = _login(client)
        response = client.post(
            "/api/control/v1/operations/checks/projection-status", headers=headers, json={}
        )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["result"] == "attention_required"
    assert response.json()["details"]["main_projection_exists"] is False
    assert context.memory.markdown.main_path.exists() is False


def test_phase7_page_services_have_constant_bounded_query_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _app = _build_phase7_app(tmp_path)
    assert context.control_plane is not None

    async def seed() -> None:
        await _seed_managed_chat(context)
        async with session_scope(context.session_factory) as session:
            knowledge = KnowledgeRepository(session)
            events = EventRepository(session)
            for index in range(150):
                await knowledge.add(
                    scope=Scope(-1001, None),
                    kind="summary",
                    content=f"Bounded item {index}",
                    source_message_ids=[],
                )
                await events.add(
                    event_type="phase7_query_count",
                    scope=Scope(-1001, None),
                    user_id=111,
                    payload={"sequence": index},
                )

    asyncio.run(seed())

    def unexpected_export(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("page services must not initialize an export")

    monkeypatch.setattr(context.memory, "export_memory_chunks", unexpected_export)

    async def measure() -> tuple[int, int, int, int, int, int]:
        memory_page, memory_queries = await _measure_queries(
            context, lambda: context.memory.browse_items_page(Scope(-1001, None), limit=51)
        )
        audit_page, audit_queries = await _measure_queries(
            context,
            lambda: context.control_plane.audit.events(
                scope=Scope(-1001, None), event_type="phase7_query_count", before_id=None, limit=50
            ),
        )
        overview, overview_queries = await _measure_queries(
            context, lambda: context.control_plane.insights.overview(Scope(-1001, None))
        )
        timeseries, timeseries_queries = await _measure_queries(
            context, lambda: context.control_plane.insights.timeseries(Scope(-1001, None), days=31)
        )
        assert len(memory_page) == 51
        assert len(audit_page["items"]) == 50
        assert audit_page["has_more"] is True
        assert overview["memory"]["knowledge_items"] == 150
        assert len(timeseries["points"]) == 31
        return (
            memory_queries,
            audit_queries,
            overview_queries,
            timeseries_queries,
            len(memory_page),
            len(audit_page["items"]),
        )

    assert asyncio.run(measure()) == (1, 1, 7, 3, 51, 50)
