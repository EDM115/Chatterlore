from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from chatterlore.db.models import Embedding, EmbeddingSpace
from chatterlore.embedding_adoption import LegacyAdoptionManifest
from chatterlore.memory.scope import Scope
from chatterlore.memory.vector import decode_vector

from .embedding_indexes import EmbeddingIndexRepository, EmbeddingSource


@dataclass(frozen=True)
class LegacyAdoptionCandidate:
    row: Embedding
    source: EmbeddingSource


class LegacyAdoptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.indexes = EmbeddingIndexRepository(session)

    async def inspect(
        self, manifest: LegacyAdoptionManifest, scope: Scope, *, space_key: str, provider_kind: str
    ) -> list[LegacyAdoptionCandidate]:
        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(Embedding).where(
                        Embedding.id.in_([entry.embedding_id for entry in manifest.entries])
                    )
                )
            ).all()
        }
        candidates: list[LegacyAdoptionCandidate] = []
        targets: set[tuple[str, int]] = set()
        for evidence in sorted(manifest.entries, key=lambda item: item.embedding_id):
            row = rows.get(evidence.embedding_id)
            if (
                row is None
                or row.space_id is not None
                or row.provenance_status != "legacy_unverified"
            ):
                raise ValueError("embedding_adoption_row_changed")
            if (
                row.provider != provider_kind
                or row.model != manifest.model
                or row.dimension != manifest.dimension
            ):
                raise ValueError("embedding_adoption_provenance_invalid")
            if (
                len(row.vector) != 4 * manifest.dimension
                or hashlib.sha256(row.vector).hexdigest() != evidence.vector_sha256
            ):
                raise ValueError("embedding_adoption_vector_invalid")
            if any(not math.isfinite(value) for value in decode_vector(row.vector)):
                raise ValueError("embedding_adoption_vector_invalid")
            source = await self.indexes.source(row.target_type, row.target_id)
            if (
                source is None
                or not scope.contains(source.scope)
                or source.scope != Scope(row.chat_id, row.message_thread_id)
                or source.source_hash != evidence.source_sha256
            ):
                raise ValueError("embedding_adoption_source_changed")
            target = (row.target_type, row.target_id)
            if target in targets:
                raise ValueError("embedding_adoption_target_exists")
            targets.add(target)
            candidates.append(LegacyAdoptionCandidate(row, source))
        space = await self.indexes.get_space(space_key)
        if (
            space is not None
            and await self.session.scalar(
                select(Embedding.id)
                .where(
                    Embedding.space_id == space.id,
                    tuple_(Embedding.target_type, Embedding.target_id).in_(targets),
                )
                .limit(1)
            )
            is not None
        ):
            raise ValueError("embedding_adoption_target_exists")
        return candidates

    async def adopt(self, candidates: list[LegacyAdoptionCandidate], space: EmbeddingSpace) -> None:
        for candidate in candidates:
            await self.indexes.repair_source_hash(candidate.source)
            candidate.row.space_id = space.id
            candidate.row.source_hash = candidate.source.source_hash
            candidate.row.provenance_status = "verified"
        await self.session.flush()
