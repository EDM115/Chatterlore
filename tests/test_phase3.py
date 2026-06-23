from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from pzp_ai_bot.ai.factory import ProviderRegistry, ProviderSelection
from pzp_ai_bot.ai.local import LocalSentenceTransformerEmbeddingProvider
from pzp_ai_bot.ai.openai_compatible import OpenAICompatibleProvider
from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult
from pzp_ai_bot.config import Settings, get_settings
from pzp_ai_bot.db.models import AIUsage
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.main import app
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import AIRequestOptions, IncomingMessage

runner = CliRunner()


class FlakyCreate:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("temporary provider outage")
        return self.response


class RecordingProvider:
    name = "recording"
    model = "recording-chat"
    embedding_model = "recording-embedding"
    dimensions = 3

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(
        self, messages, *, temperature: float = 0.2, max_tokens: int = 700
    ) -> ChatResult:
        joined = "\n".join(message.content for message in messages)
        self.prompts.append(joined)
        return ChatResult(
            content="recorded answer",
            model=self.model,
            prompt_tokens=len(joined.split()),
            completion_tokens=2,
        )

    async def embed(self, texts):
        return [[1.0, 0.0, 0.0] for _text in texts]


class StaticRegistry:
    def __init__(self, settings: Settings, provider: RecordingProvider) -> None:
        self.settings = settings
        self.provider = provider

    def select(
        self,
        profile_name: str | None = None,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
    ) -> ProviderSelection:
        profile = self.settings.provider_profile_by_name(profile_name)
        return ProviderSelection(
            profile=profile,
            chat_provider=self.provider,
            embedding_provider=self.provider,
            chat_model=chat_model or profile.chat_model,
            embedding_model=embedding_model or profile.embedding_model,
        )


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_openai_compatible_retries_and_redacts_chat_and_embedding_payloads() -> None:
    chat_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=2),
    )
    embedding_response = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])])
    chat_create = FlakyCreate(chat_response)
    embedding_create = FlakyCreate(embedding_response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
        embeddings=SimpleNamespace(create=embedding_create),
    )
    provider = OpenAICompatibleProvider(
        api_key="sk-test",
        base_url="https://example.test/v1",
        chat_model="chat-a",
        embedding_model="embed-a",
        retry_attempts=2,
        retry_backoff_seconds=0,
        redaction_custom_patterns=(r"ACME-\d+",),
        client=client,
    )

    result = await provider.generate(
        [
            ChatMessage(
                role="user",
                content=(
                    "email me@example.com phone +1 415 555 2671 api_key=secret sk-abcdefgh ACME-123"
                ),
            )
        ]
    )
    vectors = await provider.embed(["token:secret2 user@example.com ACME-456"])

    assert result.content == "ok"
    assert vectors == [[0.1, 0.2, 0.3]]
    assert len(chat_create.calls) == 2
    assert len(embedding_create.calls) == 2
    chat_payload = chat_create.calls[1]["messages"][0]["content"]
    embedding_payload = embedding_create.calls[1]["input"][0]
    assert "me@example.com" not in chat_payload
    assert "+1 415 555 2671" not in chat_payload
    assert "secret" not in chat_payload
    assert "ACME-123" not in chat_payload
    assert "[REDACTED_EMAIL]" in chat_payload
    assert "[REDACTED_PHONE]" in chat_payload
    assert "[REDACTED_CUSTOM]" in chat_payload
    assert "user@example.com" not in embedding_payload
    assert "secret2" not in embedding_payload


@pytest.mark.asyncio
async def test_local_embedding_provider_missing_extra_has_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalSentenceTransformerEmbeddingProvider("local-model")

    def fail_import(name: str):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(importlib, "import_module", fail_import)

    with pytest.raises(RuntimeError, match="uv sync --extra local"):
        await provider.embed(["hello"])


def test_provider_profile_missing_openai_key_fails_closed() -> None:
    settings = Settings(ai_provider="openai_compatible", openai_api_key="")

    with pytest.raises(ValueError, match="requires an API key"):
        ProviderRegistry(settings).select()


def test_doctor_reports_missing_openai_key(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("AI_PROVIDER", "openai_compatible")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'doctor.db'}")
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "memory"))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "missing OpenAI-compatible API key" in result.output


@pytest.mark.asyncio
async def test_embedding_usage_and_insufficient_context_are_recorded(context) -> None:
    scope = Scope(-100123, None)
    empty_answer = await context.memory.ask(scope, "What is known?")

    assert empty_answer.startswith("I do not have enough scoped memory")

    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.ingest(
        IncomingMessage(
            telegram_message_id=1,
            scope=scope,
            user_id=222,
            username="alice",
            text="Alpha uses blue widgets",
            created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )
    )
    await context.memory.search(scope, "blue widgets")

    async with session_scope(context.session_factory) as session:
        usages = list((await session.scalars(select(AIUsage))).all())
    operations = [usage.operation for usage in usages]
    assert "embed_query" in operations
    assert "embed_message" in operations
    embed_message = next(usage for usage in usages if usage.operation == "embed_message")
    embed_metadata = embed_message.metadata_json
    assert embed_metadata is not None
    assert embed_metadata["count"] == 1
    assert embed_metadata["dimensions"] == context.settings.fake_embedding_dimensions
    assert embed_metadata["provider_profile"] == "default"


@pytest.mark.asyncio
async def test_system_prompt_model_reasoning_and_one_shot_metadata(context) -> None:
    scope = Scope(-100456, None)
    recorder = RecordingProvider()
    context.memory.chat_provider = recorder
    context.memory.embedding_provider = recorder
    context.memory.provider_registry = StaticRegistry(context.settings, recorder)

    await context.memory.remember(scope, "Alpha decision is to use blue widgets")
    await context.memory.set_system_prompt_template(
        "ops", "Answer as an operations log.", updated_by=111
    )
    await context.memory.set_scope_settings(
        scope,
        {
            "system_prompt_template": "ops",
            "provider_profile": "default",
            "chat_model": "scope-chat",
            "embedding_model": "scope-embedding",
            "reasoning_level": "high",
        },
        updated_by=111,
    )
    answer = await context.memory.ask(
        scope,
        "What is the Alpha decision?",
        ai_options=AIRequestOptions(
            chat_model="request-chat",
            reasoning_level="low",
            system_prompt_override="Use one-shot system behavior.",
        ),
    )

    assert answer == "recorded answer"
    assert "Use one-shot system behavior." in recorder.prompts[-1]
    assert "Reasoning level setting: low." in recorder.prompts[-1]
    async with session_scope(context.session_factory) as session:
        usage = await session.scalar(
            select(AIUsage).where(AIUsage.operation == "ask").order_by(AIUsage.id.desc())
        )
    assert usage is not None
    assert usage.model == "request-chat"
    usage_metadata = usage.metadata_json
    assert usage_metadata is not None
    assert usage_metadata["system_prompt_template"] == "one_shot_override"
    assert usage_metadata["reasoning_level"] == "low"
    assert usage_metadata["chat_model"] == "request-chat"
