from __future__ import annotations

import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.ai.protocols import ChatMessage
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.memory.contracts import (
    ChatProviderExecution,
    EmbeddingGenerationResult,
    ProviderCallTelemetry,
    ProviderOperationError,
)
from pzp_ai_bot.memory.prompts import PROMPT_VERSION
from pzp_ai_bot.memory.scope import Scope


class ProviderOperations:
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
    ) -> ChatProviderExecution:
        started = time.perf_counter()
        try:
            result = await selection.chat_provider.generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            )
        except Exception as error:
            result = None
            failure: ProviderOperationError | None = ProviderOperationError.from_exception(error)
        else:
            failure = None
        telemetry = provider_call_telemetry(
            provider=selection.chat_provider.name,
            model=selection.chat_model,
            operation=operation,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=result.prompt_tokens if result is not None else None,
            completion_tokens=result.completion_tokens if result is not None else None,
            failure=failure,
            metadata=metadata,
        )
        return ChatProviderExecution(result=result, failure=failure, telemetry=telemetry)

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
        try:
            vectors = await selection.embedding_provider.embed(texts)
        except Exception as error:
            failure: ProviderOperationError | None = ProviderOperationError.from_exception(error)
        else:
            failure = None
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
        return EmbeddingGenerationResult(
            vectors=vectors,
            dimensions=dimensions,
            failure=failure,
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


__all__ = ["ProviderOperations", "provider_call_telemetry", "telemetry_scope"]
