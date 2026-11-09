from __future__ import annotations

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from chatterlore.db.models import KnowledgeItem, Message
from chatterlore.db.repos.embedding_indexes import EmbeddingIndexRepository
from chatterlore.db.repos.shared import EffectiveScopeSettings, scope_filter
from chatterlore.memory.scope import Scope

EMBEDDING_SETTING_KEYS = frozenset(
    {"embedding_provider_profile", "embedding_model", "provider_profile"}
)


class EmbeddingMigrationRequiredError(ValueError):
    code = "embedding_migration_required"

    def __init__(self) -> None:
        super().__init__(
            "embedding_migration_required: prepare and activate an embedding-index migration "
            "before changing the embedding route for stored memory"
        )


def embedding_route(settings: EffectiveScopeSettings) -> tuple[str, str]:
    return (
        settings.embedding_provider_profile or settings.provider_profile,
        settings.embedding_model,
    )


async def validate_embedding_rebind(
    session: AsyncSession,
    scope: Scope,
    before: EffectiveScopeSettings,
    after: EffectiveScopeSettings,
) -> None:
    if embedding_route(before) == embedding_route(after):
        return
    knowledge = await session.scalar(
        select(exists().select_from(KnowledgeItem).where(scope_filter(KnowledgeItem, scope)))
    )
    messages = await session.scalar(
        select(exists().where(scope_filter(Message, scope), Message.learned.is_(True)))
    )
    if knowledge or messages:
        raise EmbeddingMigrationRequiredError()
    await EmbeddingIndexRepository(session).advance_epoch()
