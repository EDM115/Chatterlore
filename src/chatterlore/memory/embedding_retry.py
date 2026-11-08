from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import wraps
from typing import Any

from chatterlore.db.repos.embedding_indexes import EmbeddingRouteChanged
from chatterlore.db.session import session_scope


async def record_discarded_embedding(
    session_factory: Any, operations: Any, error: EmbeddingRouteChanged
) -> None:
    telemetry = getattr(error, "telemetry", None)
    if telemetry is not None:
        discarded = replace(
            telemetry,
            error_type="EmbeddingRouteChanged",
            error_summary="EmbeddingRouteChanged: result discarded after routing changed",
        )
        async with session_scope(session_factory) as session:
            await operations.add_usage(session, discarded)


def retry_embedding_route[**P, T](function: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(3):
            try:
                return await function(*args, **kwargs)
            except EmbeddingRouteChanged as error:
                owner: Any = args[0]
                await record_discarded_embedding(
                    owner.session_factory, owner._provider_operations, error
                )
                await owner._refresh_provider_profiles()
                if attempt == 2:
                    raise
        raise RuntimeError("embedding route retry exhausted")

    return wrapped
