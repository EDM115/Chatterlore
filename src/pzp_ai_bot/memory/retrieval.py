from __future__ import annotations

from dataclasses import dataclass

from pzp_ai_bot.db.repositories import (
    EmbeddingRepository,
    KnowledgeRepository,
    MessageRepository,
    RetrievedItem,
)
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.vector import normalize, normalized_cosine_similarity


@dataclass(frozen=True, slots=True)
class SearchResult:
    content: str
    score: float
    source: str
    scope: Scope
    source_message_ids: list[int]


class Retriever:
    def __init__(
        self,
        *,
        embedding_repo: EmbeddingRepository,
        knowledge_repo: KnowledgeRepository,
        message_repo: MessageRepository,
    ) -> None:
        self.embedding_repo = embedding_repo
        self.knowledge_repo = knowledge_repo
        self.message_repo = message_repo

    async def search(
        self,
        query_vector: list[float],
        scope: Scope,
        top_k: int = 6,
        *,
        candidate_limit: int = 5_000,
    ) -> list[SearchResult]:
        embeddings = await self.embedding_repo.list_for_retrieval(scope, limit=candidate_limit)
        normalized_query = normalize(query_vector)
        knowledge_by_id = await self.knowledge_repo.get_many(
            [
                embedding.target_id
                for embedding, _vector in embeddings
                if embedding.target_type == "knowledge_item"
            ]
        )
        messages_by_id = await self.message_repo.get_many(
            [
                embedding.target_id
                for embedding, _vector in embeddings
                if embedding.target_type == "message"
            ]
        )
        knowledge_scored: list[RetrievedItem] = []
        message_scored: list[RetrievedItem] = []
        for embedding, vector in embeddings:
            score = normalized_cosine_similarity(normalized_query, vector)
            content = ""
            source_ids: list[int] = []
            if embedding.target_type == "knowledge_item":
                item = knowledge_by_id.get(embedding.target_id)
                if item is None:
                    continue
                content = item.content
                source_ids = list(item.source_message_ids)
            elif embedding.target_type == "message":
                message = messages_by_id.get(embedding.target_id)
                if message is None or not message.learned:
                    continue
                content = message.text
                source_ids = [message.telegram_message_id]
            else:
                continue
            target = (
                knowledge_scored if embedding.target_type == "knowledge_item" else message_scored
            )
            target.append(
                RetrievedItem(
                    target_type=embedding.target_type,
                    target_id=embedding.target_id,
                    content=content,
                    score=score,
                    source_message_ids=source_ids,
                    scope=Scope(embedding.chat_id, embedding.message_thread_id),
                )
            )
        knowledge_scored.sort(key=lambda item: item.score, reverse=True)
        message_scored.sort(key=lambda item: item.score, reverse=True)
        scored = knowledge_scored[:top_k]
        if len(scored) < top_k:
            scored.extend(message_scored[: top_k - len(scored)])
        results = [
            SearchResult(
                content=item.content,
                score=item.score,
                source=f"{item.target_type}:{item.target_id}",
                scope=item.scope,
                source_message_ids=item.source_message_ids,
            )
            for item in scored[:top_k]
        ]
        if results:
            return results
        recent = await self.message_repo.recent(scope, limit=min(3, top_k), learned_only=True)
        return [
            SearchResult(
                content=message.text,
                score=0.0,
                source=f"message:{message.id}",
                scope=Scope(message.chat_id, message.message_thread_id),
                source_message_ids=[message.telegram_message_id],
            )
            for message in recent
        ]
