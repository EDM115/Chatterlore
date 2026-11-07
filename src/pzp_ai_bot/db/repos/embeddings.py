from __future__ import annotations

import math

from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import Embedding, KnowledgeItem, Message
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.vector import decode_vector, encode_vector

from .embedding_indexes import EmbeddingIndexRepository, EmbeddingSourceChanged
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
        space: dict | None = None,
        source_hash: str | None = None,
        route_epoch: int | None = None,
    ) -> Embedding:
        if not vector or any(
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError("embedding_vector_invalid")
        if space is not None:
            indexes = EmbeddingIndexRepository(self.session)
            await indexes.guard_write(route_epoch)
            await indexes.validate_profile(space)
            manifest = await indexes.register_space(space)
            if manifest.dimension != len(vector) or manifest.model != model:
                raise ValueError("embedding_space_dimension_or_model_mismatch")
            source = await indexes.source(target_type, target_id)
            if source is None or source.source_hash != source_hash or source.scope != scope:
                raise EmbeddingSourceChanged()
            await indexes.repair_source_hash(source)
            values = {
                "target_type": target_type,
                "target_id": target_id,
                "chat_id": scope.chat_id,
                "message_thread_id": scope.thread_id,
                "provider": provider,
                "model": model,
                "dimension": len(vector),
                "vector": encode_vector(vector),
                "space_id": manifest.id,
                "source_hash": source_hash,
                "provenance_status": "verified",
                "created_at": utc_now(),
            }
            statement = sqlite_insert(Embedding).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=[Embedding.target_type, Embedding.target_id, Embedding.space_id],
                set_=values,
            ).returning(Embedding)
            embedding = await self.session.scalar(
                statement.execution_options(populate_existing=True)
            )
            if embedding is None:
                raise RuntimeError("embedding_upsert_failed")
            return embedding
        existing = await self.session.scalar(
            select(Embedding).where(
                Embedding.target_type == target_type,
                Embedding.target_id == target_id,
                Embedding.provider == provider,
                Embedding.model == model,
                Embedding.space_id.is_(None),
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
            space_id=None,
            source_hash=None,
            provenance_status="legacy_unverified",
            created_at=utc_now(),
        )
        self.session.add(embedding)
        await self.session.flush()
        return embedding

    async def list_for_retrieval(
        self, scope: Scope, *, limit: int = 5_000, space_id: int | None = None
    ) -> list[tuple[Embedding, list[float]]]:
        if limit < 1:
            raise ValueError("retrieval candidate limit must be positive")
        if space_id is not None:
            return await self._list_verified(scope, space_id=space_id, limit=limit)
        knowledge_stmt: Select[Embedding] = (
            select(Embedding)
            .join(KnowledgeItem, Embedding.target_id == KnowledgeItem.id)
            .where(scope_filter(Embedding, scope), Embedding.target_type == "knowledge_item")
            .order_by(Embedding.created_at.desc(), Embedding.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.scalars(knowledge_stmt)).all())
        remaining = limit - len(rows)
        if remaining > 0:
            message_stmt: Select[Embedding] = (
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

    async def _list_verified(
        self, scope: Scope, *, space_id: int, limit: int
    ) -> list[tuple[Embedding, list[float]]]:
        rows: list[tuple[Embedding, list[float]]] = []
        for model, target_type in ((KnowledgeItem, "knowledge_item"), (Message, "message")):
            statement = (
                select(Embedding)
                .join(model, Embedding.target_id == model.id)
                .where(
                    scope_filter(Embedding, scope),
                    scope_filter(model, scope),
                    Embedding.target_type == target_type,
                    Embedding.space_id == space_id,
                    Embedding.provenance_status == "verified",
                    model.embedding_source_hash.is_not(None),
                    Embedding.source_hash == model.embedding_source_hash,
                )
            )
            if model is Message:
                statement = statement.where(Message.learned.is_(True)).order_by(
                    Message.created_at.desc(), Message.id.desc(), Embedding.id.desc()
                )
            else:
                statement = statement.order_by(Embedding.created_at.desc(), Embedding.id.desc())
            # Exact space and freshness are SQL predicates before the durable-first quota
            candidates = (await self.session.scalars(statement.limit(limit - len(rows)))).all()
            rows.extend((row, decode_vector(row.vector)) for row in candidates)
            if len(rows) >= limit:
                return rows
        return rows

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
