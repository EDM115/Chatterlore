from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.ai.factory import ProviderSelection
from chatterlore.ai.protocols import ChatMessage, ChatProgressCallback
from chatterlore.config import Settings
from chatterlore.db.repos.embedding_indexes import EmbeddingIndexRepository
from chatterlore.db.repos.embeddings import EmbeddingRepository
from chatterlore.db.repos.knowledge import KnowledgeRepository
from chatterlore.db.repos.messages import MessageRepository
from chatterlore.db.repos.policy import PolicyRepository
from chatterlore.db.repos.profiles import MemoryProfileRepository
from chatterlore.db.repos.settings import ScopeSettingsRepository
from chatterlore.db.repos.shared import EffectiveScopeSettings
from chatterlore.db.session import session_scope
from chatterlore.memory.contracts import AIRequestOptions
from chatterlore.memory.policy import PolicyService
from chatterlore.memory.prompts import ANSWER_INSUFFICIENT_CONTEXT, prompt_metadata
from chatterlore.memory.provider_operations import ProviderOperations
from chatterlore.memory.retrieval import Retriever, SearchResult
from chatterlore.memory.scope import Scope
from chatterlore.memory.settings_profiles import MemorySettingsService

ProviderSelector = Callable[[EffectiveScopeSettings, AIRequestOptions | None], ProviderSelection]
ReasoningLevelResolver = Callable[[EffectiveScopeSettings, AIRequestOptions | None], str]


@dataclass(frozen=True, slots=True)
class QuerySelection:
    retrieval_provider_profile: str
    embedding_model: str
    chat_provider_profile: str | None
    chat_model: str | None
    reasoning_level: str | None


@dataclass(frozen=True, slots=True)
class SearchExecutionResult:
    destination_scope: Scope
    source_scopes: tuple[Scope, ...]
    memory_profile: str | None
    selection: QuerySelection
    results: tuple[SearchResult, ...]
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class AskExecutionResult:
    destination_scope: Scope
    source_scopes: tuple[Scope, ...]
    memory_profile: str | None
    selection: QuerySelection
    results: tuple[SearchResult, ...]
    answer: str
    insufficient_context: bool
    elapsed_ms: int
    generation_interrupted: bool = False


class EmbeddingCoverageRequiredError(ValueError):
    def __init__(self, scope: Scope, missing: int) -> None:
        self.scope = scope
        self.missing = missing
        super().__init__(
            f"Embedding index is not ready for {scope.key} ({missing} missing or stale sources). "
            "Ask a bot administrator to rebuild this embedding index."
        )


class MemoryRetrievalService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker,
        settings_profiles: MemorySettingsService,
        select_retrieval_provider: ProviderSelector,
        select_chat_provider: ProviderSelector,
        reasoning_level: ReasoningLevelResolver,
        provider_operations: ProviderOperations,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.settings_profiles = settings_profiles
        self.select_retrieval_provider = select_retrieval_provider
        self.select_chat_provider = select_chat_provider
        self.reasoning_level = reasoning_level
        self.provider_operations = provider_operations

    async def ask(
        self,
        scope: Scope,
        question: str,
        *,
        top_k: int | None = None,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
        on_text: ChatProgressCallback | None = None,
    ) -> str:
        return (
            await self.ask_with_metadata(
                scope,
                question,
                top_k=top_k,
                profile_name=profile_name,
                ai_options=ai_options,
                on_text=on_text,
            )
        ).answer

    async def ask_with_metadata(
        self,
        scope: Scope,
        question: str,
        *,
        top_k: int | None = None,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
        on_text: ChatProgressCallback | None = None,
    ) -> AskExecutionResult:
        started = time.perf_counter()
        scope_settings = await self.settings_profiles.get_scope_settings(scope)
        if top_k is None:
            top_k = scope_settings.retrieval_top_k
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self.settings_profiles.default_scope_settings()
            )
            selection = self.select_chat_provider(scope_settings, ai_options)
            reasoning_level = self.reasoning_level(scope_settings, ai_options)
            system_template = await self.settings_profiles.answer_system_prompt_in_session(
                session, scope_settings, ai_options
            )
        search_execution = await self.search_with_metadata(
            scope, question, top_k=top_k, profile_name=profile_name, ai_options=ai_options
        )
        results = list(search_execution.results)
        query_selection = QuerySelection(
            retrieval_provider_profile=search_execution.selection.retrieval_provider_profile,
            embedding_model=search_execution.selection.embedding_model,
            chat_provider_profile=selection.profile.name,
            chat_model=selection.chat_model,
            reasoning_level=reasoning_level,
        )
        best_score = max((result.score for result in results), default=0.0)
        if not results or best_score < float(
            self.settings_profiles.runtime_setting("answer_min_score")
        ):
            return AskExecutionResult(
                destination_scope=scope,
                source_scopes=search_execution.source_scopes,
                memory_profile=profile_name,
                selection=query_selection,
                results=tuple(results),
                answer=(
                    f"{ANSWER_INSUFFICIENT_CONTEXT} Best scoped-memory score: {best_score:.3f}."
                ),
                insufficient_context=True,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
        context = format_results(results)
        messages = [
            ChatMessage(
                role="system",
                content=(
                    f"{system_template.prompt}\n"
                    f"Prompt version: {system_template.version}.\n"
                    f"Reasoning level setting: {reasoning_level}."
                ),
            ),
            ChatMessage(role="user", content=f"Question: {question}\n\nScoped memory:\n{context}"),
        ]
        execution = await self.provider_operations.execute_chat(
            selection,
            messages,
            operation="ask",
            metadata={
                **prompt_metadata(
                    template_name=system_template.name,
                    template_version=system_template.version,
                    reasoning_level=reasoning_level,
                    provider_profile=selection.profile.name,
                ),
                "chat_model": selection.chat_model,
                "embedding_model": selection.embedding_model,
                "scope": scope.key,
                "retrieval_results": len(results),
                "best_score": best_score,
                "memory_profile": profile_name,
            },
            temperature=0.1,
            max_tokens=700,
            reasoning_effort=reasoning_level,
            on_text=on_text,
        )
        content = (
            execution.result.content if execution.result is not None else fallback_answer(results)
        )
        if execution.stream_interrupted:
            content = (
                "Generation was interrupted. No completed answer was produced. Please try again."
            )
        elif execution.failure is not None and execution.failure.error_type == "EmptyChatResponse":
            content = (
                "The model returned no answer text. "
                "Try a shorter request or lower reasoning effort.\n\n" + fallback_answer(results)
            )
        async with session_scope(self.session_factory) as session:
            await self.provider_operations.add_usage(session, execution.telemetry)
        return AskExecutionResult(
            destination_scope=scope,
            source_scopes=search_execution.source_scopes,
            memory_profile=profile_name,
            selection=query_selection,
            results=tuple(results),
            answer=content
            if execution.stream_interrupted
            else append_answer_sources(content, results),
            insufficient_context=False,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            generation_interrupted=execution.stream_interrupted,
        )

    async def search(
        self,
        scope: Scope,
        query: str,
        *,
        top_k: int = 6,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> list[SearchResult]:
        return list(
            (
                await self.search_with_metadata(
                    scope, query, top_k=top_k, profile_name=profile_name, ai_options=ai_options
                )
            ).results
        )

    async def search_with_metadata(
        self,
        scope: Scope,
        query: str,
        *,
        top_k: int = 6,
        profile_name: str | None = None,
        ai_options: AIRequestOptions | None = None,
    ) -> SearchExecutionResult:
        started = time.perf_counter()
        async with session_scope(self.session_factory) as session:
            scope_settings = await ScopeSettingsRepository(session).effective(
                scope, self.settings_profiles.default_scope_settings()
            )
            selection = self.select_retrieval_provider(scope_settings, ai_options)
            scopes = await self.retrieval_scopes_in_session(session, scope, profile_name)
        embedding_generation = await self.provider_operations.generate_embeddings(
            selection,
            [query],
            operation="embed_query",
            metadata={"scope": scope.key, "memory_profile": profile_name},
        )
        if embedding_generation.error is not None:
            async with session_scope(self.session_factory) as session:
                await self.provider_operations.add_embedding_usage(
                    session,
                    selection=selection,
                    generation=embedding_generation,
                    texts_count=1,
                    operation="embed_query",
                    metadata={"scope": scope.key, "memory_profile": profile_name},
                )
            raise embedding_generation.error
        query_vector = embedding_generation.vectors[0]
        async with session_scope(self.session_factory) as session:
            await self.provider_operations.add_embedding_usage(
                session,
                selection=selection,
                generation=embedding_generation,
                texts_count=1,
                operation="embed_query",
                metadata={"scope": scope.key, "memory_profile": profile_name},
            )
        async with session_scope(self.session_factory) as session:
            retriever = Retriever(
                embedding_repo=EmbeddingRepository(session),
                knowledge_repo=KnowledgeRepository(session),
                message_repo=MessageRepository(session),
            )
            results: list[SearchResult] = []
            indexes = EmbeddingIndexRepository(session)
            if embedding_generation.space is None:
                raise ValueError("query embedding space is unavailable")
            space = await indexes.get_space(embedding_generation.space["key"])
            for source_scope in scopes:
                coverage = await indexes.coverage(
                    source_scope, space.id if space is not None else None
                )
                if coverage["missing"]:
                    raise EmbeddingCoverageRequiredError(source_scope, coverage["missing"])
                if space is None:
                    continue
                results.extend(
                    await retriever.search(
                        query_vector,
                        source_scope,
                        top_k,
                        candidate_limit=int(
                            self.settings_profiles.runtime_setting("retrieval_candidate_limit")
                        ),
                        space_id=space.id,
                    )
                )
            results.sort(key=search_result_rank)
            bounded_results = tuple(results[:top_k])
        return SearchExecutionResult(
            destination_scope=scope,
            source_scopes=tuple(scopes),
            memory_profile=profile_name,
            selection=QuerySelection(
                retrieval_provider_profile=selection.profile.name,
                embedding_model=selection.embedding_model,
                chat_provider_profile=None,
                chat_model=None,
                reasoning_level=None,
            ),
            results=bounded_results,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    async def retrieval_scopes_in_session(
        self, session: AsyncSession, destination: Scope, profile_name: str | None
    ) -> list[Scope]:
        if profile_name is None:
            return [destination]
        profile_repo = MemoryProfileRepository(session)
        profile = await profile_repo.get_profile(profile_name)
        if profile is None:
            raise ValueError(f"profile {profile_name!r} was not found")
        policy = PolicyService(
            self.settings,
            PolicyRepository(session),
            learning_default_enabled=self.settings_profiles.runtime_setting(
                "learning_default_enabled"
            ),
        )
        scopes = [destination]
        for source in profile.source_scopes:
            if source == destination:
                continue
            if not await profile_repo.can_query(destination=destination, source=source):
                continue
            if (await policy.status(source)).get("learning_enabled") is not True:
                continue
            scopes.append(source)
        if len(scopes) == 1:
            raise ValueError(
                f"profile {profile.name!r} has no permitted learning-enabled source scopes "
                f"for {destination.key}"
            )
        return dedupe_scope_list(scopes)


def dedupe_scope_list(scopes: list[Scope]) -> list[Scope]:
    seen: set[str] = set()
    out: list[Scope] = []
    for scope in scopes:
        if scope.key in seen:
            continue
        seen.add(scope.key)
        out.append(scope)
    return out


def search_result_rank(result: SearchResult) -> tuple[int, float]:
    tier = 0 if result.source.startswith("knowledge_item:") else 1
    return tier, -result.score


def format_results(results: list[SearchResult]) -> str:
    if not results:
        return "No scoped memory found."
    lines = []
    for result in results:
        sources = ", ".join(str(source) for source in result.source_message_ids) or result.source
        lines.append(
            f"[source:{sources} score:{result.score:.3f} scope:{result.scope.key}] {result.content}"
        )
    return "\n".join(lines)


def compact_answer_sources(results: list[SearchResult], *, limit: int = 8) -> str:
    refs: list[str] = []
    seen: set[str] = set()
    total_refs = sum(
        len(item.source_message_ids) if item.source_message_ids else 1 for item in results
    )
    for result in results:
        source_refs = [str(source_id) for source_id in result.source_message_ids] or [result.source]
        for source_ref in source_refs:
            ref = f"{result.scope.key}#{source_ref}"
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
            if len(refs) >= limit:
                suffix = " + more" if len(seen) < total_refs else ""
                return ", ".join(refs) + suffix
    return ", ".join(refs)


def append_answer_sources(answer: str, results: list[SearchResult]) -> str:
    sources = compact_answer_sources(results)
    if not sources:
        return answer
    lower = answer.lower()
    if "sources:" in lower or "[source:" in lower:
        return answer
    return f"{answer.rstrip()}\n\nSources: {sources}"


def fallback_answer(results: list[SearchResult]) -> str:
    if not results:
        return "I do not have scoped memory for that yet."
    return "From scoped memory:\n" + format_results(results)
