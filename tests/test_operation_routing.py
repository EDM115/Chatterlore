from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.ai.fake import FakeProvider
from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.models import AIUsage, Embedding, KnowledgeItem
from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository
from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.contracts import IncomingMessage
from pzp_ai_bot.memory.scope import Scope


async def test_operation_profiles_select_own_models_without_cross_vendor_inheritance(context):
    profiles = {
        "answers": {"provider": "fake", "chat_model": "answer-model"},
        "summaries": {"provider": "fake", "chat_model": "summary-model"},
        "vectors": {"provider": "fake", "embedding_model": "vector-model"},
    }
    settings = Settings(ai_provider_profiles=json.dumps(profiles))
    registry = ProviderRegistry(settings)
    context.memory.provider_registry = registry
    context.memory._settings_profiles.profile_models = registry.profile_models
    scope = Scope(-2201, None)
    await context.memory.set_scope_settings(
        scope,
        {
            "chat_provider_profile": "answers",
            "summary_provider_profile": "summaries",
            "embedding_provider_profile": "vectors",
        },
    )
    effective = await context.memory.get_scope_settings(scope)
    assert context.memory._select_chat_provider(effective).chat_model == "answer-model"
    assert context.memory._select_provider(effective, summary=True).chat_model == "summary-model"
    assert context.memory._select_embedding_provider(effective).embedding_model == "vector-model"
    assert effective.chat_model == "answer-model"
    assert effective.summary_model == "summary-model"
    assert effective.embedding_model == "vector-model"


async def test_profile_boundary_keeps_same_layer_model_override_and_reset_restores_profile(context):
    profiles = {"other": {"provider": "fake", "chat_model": "other-default"}}
    registry = ProviderRegistry(Settings(ai_provider_profiles=json.dumps(profiles)))
    context.memory.provider_registry = registry
    context.memory._settings_profiles.profile_models = registry.profile_models
    await context.memory.set_scope_settings(Scope.global_scope(), {"chat_model": "global-model"})
    chat = Scope(-2202, None)
    await context.memory.set_scope_settings(
        chat, {"chat_provider_profile": "other", "chat_model": "chat-override"}
    )
    assert (await context.memory.get_scope_settings(chat)).chat_model == "chat-override"
    await context.memory.set_scope_settings(chat, {"chat_model": None})
    effective = await context.memory.get_scope_settings(chat)
    assert effective.chat_model == "other-default"
    topic = Scope(-2202, 7)
    await context.memory.set_scope_settings(topic, {"chat_model": "topic-model"})
    assert (await context.memory.get_scope_settings(topic)).chat_model == "topic-model"
    async with session_scope(context.session_factory) as session:
        stored = await ScopeSettingsRepository(session).get(chat)
        assert stored is not None and stored.chat_model is None


async def test_populated_scope_cannot_bypass_embedding_migration_through_settings(context):
    scope = Scope(-2203, None)
    async with session_scope(context.session_factory) as session:
        await KnowledgeRepository(session).add(
            scope=scope, kind="manual_note", content="Preserved source", source_message_ids=[]
        )
    with pytest.raises(ValueError, match="embedding_migration_required"):
        await context.memory.set_scope_settings(scope, {"embedding_model": "different-space"})
    assert (await context.memory.get_scope_settings(scope)).embedding_model == "fake-embedding"


async def test_messages_notes_summaries_and_asks_use_independent_operation_routes(context):
    registry = ProviderRegistry(
        Settings(
            ai_provider_profiles=json.dumps(
                {
                    "answers": {"provider": "fake", "chat_model": "answer-model"},
                    "summaries": {"provider": "fake", "chat_model": "summary-model"},
                    "vectors": {"provider": "fake", "embedding_model": "vector-model"},
                }
            )
        )
    )
    context.memory.provider_registry = registry
    context.memory._settings_profiles.profile_models = registry.profile_models
    scope = Scope(-2204, None)
    await context.memory.set_scope_settings(
        scope,
        {
            "chat_provider_profile": "answers",
            "summary_provider_profile": "summaries",
            "embedding_provider_profile": "vectors",
        },
    )
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.remember(scope, "Project Pine uses green widgets")
    for message_id in (1, 2):
        await context.memory.ingest(
            IncomingMessage(
                telegram_message_id=message_id,
                scope=scope,
                user_id=111,
                username="owner",
                text="Project Pine uses green widgets",
                created_at=datetime.now(UTC),
            )
        )
    await context.memory.ask(scope, "Project Pine uses green widgets")
    async with session_scope(context.session_factory) as session:
        usage = list((await session.scalars(select(AIUsage))).all())
        assert [row.model for row in usage if row.operation == "ask"] == ["answer-model"]
        assert [row.model for row in usage if row.operation == "summarize"] == ["summary-model"]
        assert {row.model for row in usage if row.operation.startswith("embed_")} == {
            "vector-model"
        }
        assert set((await session.scalars(select(Embedding.model))).all()) == {"vector-model"}


async def test_inflight_embedding_is_retried_after_route_epoch_changes(context):
    class EpochProvider(FakeProvider):
        calls = 0

        async def embed(self, texts):
            self.calls += 1
            if self.calls == 1:
                async with session_scope(context.session_factory) as session:
                    await EmbeddingIndexRepository(session).advance_epoch()
            return await super().embed(texts)

    provider = EpochProvider()
    context.memory.embedding_provider = provider
    scope = Scope(-2205, None)
    await context.memory.remember(scope, "Created once despite an index activation race")
    assert provider.calls == 2
    async with session_scope(context.session_factory) as session:
        assert await session.scalar(select(func.count(KnowledgeItem.id))) == 1
        usages = list(
            (
                await session.scalars(select(AIUsage).where(AIUsage.operation == "embed_knowledge"))
            ).all()
        )
        assert len(usages) == 2
        assert sum(row.error is not None for row in usages) == 1
