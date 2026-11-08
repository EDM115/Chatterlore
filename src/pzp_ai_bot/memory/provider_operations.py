from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.ai.openai_compatible import resolve_api_dialect, supports_reasoning_effort
from pzp_ai_bot.ai.protocols import ChatMessage, ChatProgressCallback
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.embedding_identity import content_hash, embedding_space_metadata
from pzp_ai_bot.memory.contracts import (
    ChatProviderExecution,
    EmbeddingGenerationResult,
    ProviderCallTelemetry,
    ProviderOperationError,
)
from pzp_ai_bot.memory.prompts import PROMPT_VERSION
from pzp_ai_bot.memory.scope import Scope

logger = structlog.get_logger(__name__)


class ProviderOperations:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self.session_factory = session_factory

    async def execute_chat(
        self,
        selection: ProviderSelection,
        messages: list[ChatMessage],
        *,
        operation: str,
        metadata: dict[str, Any],
        temperature: float,
        max_tokens: int,
        reasoning_effort: str | None,
        on_text: ChatProgressCallback | None = None,
    ) -> ChatProviderExecution:
        started = time.perf_counter()
        effective_reasoning: str | None = None
        if selection.profile.reasoning_capable:
            if selection.profile.provider == "fake":
                effective_reasoning = reasoning_effort
            elif selection.profile.provider == "openai_compatible":
                dialect = resolve_api_dialect(
                    selection.profile.api_dialect,
                    base_url=selection.profile.openai_base_url,
                    model=selection.chat_model,
                    local=selection.profile.local,
                )
                if dialect == "deepseek":
                    effective_reasoning = (
                        "high" if reasoning_effort == "medium" else reasoning_effort
                    )
                elif dialect == "openai" and supports_reasoning_effort(selection.chat_model):
                    effective_reasoning = reasoning_effort

        emitted_text = False

        async def notify(text: str) -> None:
            nonlocal emitted_text
            emitted_text = emitted_text or bool(text)
            if on_text is None:
                return
            try:
                await on_text(text)
            except Exception as error:
                logger.warning("chat_progress_failed", error_type=type(error).__name__)

        cancelled = False
        response_empty = False
        stream = getattr(selection.chat_provider, "generate_stream", None)
        streaming = on_text is not None and callable(stream)
        try:
            kwargs = {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "reasoning_effort": effective_reasoning,
            }
            if streaming:
                result = await stream(messages, on_text=notify, **kwargs)
            else:
                result = await selection.chat_provider.generate(messages, **kwargs)
        except asyncio.CancelledError:
            result = None
            cancelled = True
            failure: ProviderOperationError | None = ProviderOperationError(
                error_type="CancelledError", safe_summary="CancelledError: generation cancelled"
            )
        except Exception as error:
            result = None
            failure = ProviderOperationError.from_exception(error)
        else:
            response_empty = not result.content.strip()
            failure = (
                ProviderOperationError(
                    error_type="EmptyChatResponse",
                    safe_summary="EmptyChatResponse: provider returned no answer text",
                )
                if response_empty
                else None
            )
        attempt_metadata = _consume_attempt_metadata(selection.chat_provider)
        usage_metadata = dict(metadata)
        usage_metadata["requested_reasoning_level"] = reasoning_effort
        usage_metadata["effective_reasoning_level"] = effective_reasoning
        usage_metadata["streaming"] = streaming
        if response_empty:
            usage_metadata["response_empty"] = True
        stream_interrupted = streaming and emitted_text and failure is not None
        usage_metadata["stream_interrupted"] = stream_interrupted
        if attempt_metadata is not None:
            usage_metadata.update(attempt_metadata.as_dict())
        telemetry = provider_call_telemetry(
            provider=selection.chat_provider.name,
            model=selection.chat_model,
            operation=operation,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=result.prompt_tokens if result is not None else None,
            completion_tokens=result.completion_tokens if result is not None else None,
            failure=failure,
            metadata=usage_metadata,
        )
        if cancelled:
            if self.session_factory is not None:
                try:
                    async with session_scope(self.session_factory) as session:
                        await self.add_usage(session, telemetry)
                except Exception as error:
                    logger.warning("cancelled_chat_usage_failed", error_type=type(error).__name__)
            raise asyncio.CancelledError
        return ChatProviderExecution(
            result=None if failure is not None else result,
            failure=failure,
            telemetry=telemetry,
            stream_interrupted=stream_interrupted,
        )

    async def generate_embeddings(
        self,
        selection: ProviderSelection,
        texts: list[str],
        *,
        operation: str,
        metadata: dict[str, Any],
    ) -> EmbeddingGenerationResult:
        started = time.perf_counter()
        vectors: list[list[float]] = []
        route_epoch = selection.route_epoch
        if route_epoch is None and self.session_factory is not None:
            from pzp_ai_bot.db.repos.embedding_indexes import EmbeddingIndexRepository

            async with session_scope(self.session_factory) as session:
                route_epoch = await EmbeddingIndexRepository(session).current_epoch()
        try:
            purpose_method = "embed_query" if operation == "embed_query" else "embed_documents"
            embed = getattr(
                selection.embedding_provider, purpose_method, selection.embedding_provider.embed
            )
            vectors = await embed(texts)
            if len(vectors) != len(texts):
                raise ValueError("embedding response count does not match input count")
            if vectors and (not vectors[0] or len(vectors[0]) > 65_536):
                raise ValueError("embedding dimensions are invalid")
            if vectors and any(len(vector) != len(vectors[0]) for vector in vectors):
                raise ValueError("embedding response dimensions are inconsistent")
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for vector in vectors
                for value in vector
            ):
                raise ValueError("embedding response contains invalid numeric values")
        except Exception as error:
            failure: ProviderOperationError | None = ProviderOperationError.from_exception(error)
        else:
            failure = None
        attempt_metadata = _consume_attempt_metadata(selection.embedding_provider)
        dimensions = (
            len(vectors[0])
            if vectors
            else getattr(selection.embedding_provider, "dimensions", None)
        )
        usage_metadata = {
            "count": len(texts),
            "dimensions": dimensions,
            "provider_profile": selection.profile.name,
            "embedding_model": selection.embedding_model,
            "prompt_version": PROMPT_VERSION,
            **metadata,
        }
        if attempt_metadata is not None:
            usage_metadata.update(attempt_metadata.as_dict())
        return EmbeddingGenerationResult(
            vectors=vectors,
            dimensions=dimensions,
            failure=failure,
            space=embedding_space_metadata(selection, dimensions)
            if failure is None and dimensions and vectors
            else None,
            source_hashes=tuple(content_hash(text) for text in texts),
            route_epoch=route_epoch,
            telemetry=provider_call_telemetry(
                provider=selection.embedding_provider.name,
                model=selection.embedding_model,
                operation=operation,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=None,
                completion_tokens=None,
                failure=failure,
                metadata=usage_metadata,
            ),
        )

    async def add_embedding_usage(
        self,
        session: AsyncSession,
        *,
        selection: ProviderSelection,
        generation: EmbeddingGenerationResult,
        texts_count: int,
        operation: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if (
            generation.failure is None
            and operation != "embed_query"
            and generation.route_epoch is not None
        ):
            from pzp_ai_bot.db.repos.embedding_indexes import (
                EmbeddingIndexRepository,
                EmbeddingRouteChanged,
            )

            try:
                await EmbeddingIndexRepository(session).guard_write(generation.route_epoch)
            except EmbeddingRouteChanged as error:
                error.telemetry = generation.telemetry
                raise
        if (
            generation.telemetry.provider != selection.embedding_provider.name
            or generation.telemetry.model != selection.embedding_model
            or generation.telemetry.operation != operation
            or dict(generation.telemetry.metadata).get("count") != texts_count
            or any(
                dict(generation.telemetry.metadata).get(key) != value
                for key, value in (metadata or {}).items()
            )
        ):
            raise RuntimeError("embedding telemetry does not match its persistence boundary")
        await self.add_usage(session, generation.telemetry)

    async def add_usage(self, session: AsyncSession, telemetry: ProviderCallTelemetry) -> None:
        metadata = dict(telemetry.metadata)
        await EventRepository(session).add_ai_usage(
            scope=telemetry_scope(metadata),
            provider=telemetry.provider,
            model=telemetry.model,
            operation=telemetry.operation,
            prompt_tokens=telemetry.prompt_tokens,
            completion_tokens=telemetry.completion_tokens,
            latency_ms=telemetry.latency_ms,
            error=telemetry.error_summary,
            metadata_json=metadata,
        )


def provider_call_telemetry(
    *,
    provider: str,
    model: str,
    operation: str,
    latency_ms: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    failure: ProviderOperationError | None,
    metadata: dict[str, Any],
) -> ProviderCallTelemetry:
    safe_metadata = dict(metadata)
    if failure is not None:
        safe_metadata["error_type"] = failure.error_type
    return ProviderCallTelemetry(
        provider=provider,
        model=model,
        operation=operation,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_type=failure.error_type if failure is not None else None,
        error_summary=failure.safe_summary if failure is not None else None,
        metadata=tuple(safe_metadata.items()),
    )


def telemetry_scope(metadata: dict[str, Any]) -> Scope | None:
    raw_scope = metadata.get("scope")
    if not isinstance(raw_scope, str):
        return None
    clean = raw_scope.strip().lower()
    if clean in {"all", "all:main"}:
        return Scope.global_scope()
    parts = clean.split(":")
    if len(parts) not in {1, 2}:
        return None
    try:
        chat_id = int(parts[0])
        thread_id = None if len(parts) == 1 or parts[1] in {"", "main", "none"} else int(parts[1])
    except ValueError:
        return None
    return Scope(chat_id, thread_id)


def _consume_attempt_metadata(provider: object) -> Any | None:
    consume = getattr(provider, "consume_attempt_metadata", None)
    if not callable(consume):
        return None
    return consume()


__all__ = ["ProviderOperations", "provider_call_telemetry", "telemetry_scope"]
