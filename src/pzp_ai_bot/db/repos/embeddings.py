from __future__ import annotations

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import Embedding, KnowledgeItem, Message
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.vector import decode_vector, encode_vector

from .shared import scope_filter, utc_now


class EmbeddingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self,
        *,
        target_type: str,
        target_id: int,
        scope: Scope,
        provider: str,
        model: str,
        vector: list[float],
    ) -> Embedding:
        existing = await self.session.scalar(
            select(Embedding).where(
                Embedding.target_type == target_type,
                Embedding.target_id == target_id,
                Embedding.provider == provider,
                Embedding.model == model,
            )
        )
        if existing is not None:
            return existing
        embedding = Embedding(
            target_type=target_type,
            target_id=target_id,
            chat_id=scope.chat_id,
            message_thread_id=scope.thread_id,
            provider=provider,
            model=model,
            dimension=len(vector),
            vector=encode_vector(vector),
            created_at=utc_now(),
        )
        self.session.add(embedding)
        await self.session.flush()
        return embedding

    async def list_for_retrieval(
        self, scope: Scope, *, limit: int = 5_000
    ) -> list[tuple[Embedding, list[float]]]:
        if limit < 1:
            raise ValueError("retrieval candidate limit must be positive")
        knowledge_stmt: Select[tuple[Embedding]] = (
            select(Embedding)
            .join(KnowledgeItem, Embedding.target_id == KnowledgeItem.id)
            .where(scope_filter(Embedding, scope), Embedding.target_type == "knowledge_item")
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.scalars(knowledge_stmt)).all())
        remaining = limit - len(rows)
        if remaining > 0:
            message_stmt: Select[tuple[Embedding]] = (
                select(Embedding)
                .join(Message, Embedding.target_id == Message.id)
                .where(
                    scope_filter(Embedding, scope),
                    Embedding.target_type == "message",
                    Message.learned.is_(True),
                )
                .order_by(Message.created_at.desc(), Message.id.desc(), Embedding.id.desc())
                .limit(remaining)
            )
            rows.extend(list((await self.session.scalars(message_stmt)).all()))
        return [(row, decode_vector(row.vector)) for row in rows]

    async def count(self, scope: Scope | None = None) -> int:
        stmt = select(func.count(Embedding.id))
        if scope is not None:
            stmt = stmt.where(scope_filter(Embedding, scope))
        return int(await self.session.scalar(stmt) or 0)

    async def delete_targets(self, *, target_type: str, target_ids: list[int]) -> int:
        if not target_ids:
            return 0
        count = await self.session.scalar(
            select(func.count(Embedding.id)).where(
                Embedding.target_type == target_type, Embedding.target_id.in_(target_ids)
            )
        )
        await self.session.execute(
            delete(Embedding).where(
                Embedding.target_type == target_type, Embedding.target_id.in_(target_ids)
            )
        )
        return int(count or 0)

    async def delete_scope(self, scope: Scope) -> int:
        count = await self.session.scalar(
            select(func.count(Embedding.id)).where(scope_filter(Embedding, scope))
        )
        await self.session.execute(delete(Embedding).where(scope_filter(Embedding, scope)))
        return int(count or 0)
