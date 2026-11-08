from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram.types import Message

from chatterlore.memory.scope import Scope

AdminGuard = Callable[[Message], Awaitable[bool]]
ScopeAdminGuard = Callable[[Message, Scope], Awaitable[bool]]


@dataclass(frozen=True)
class RouterGuards:
    require_admin: AdminGuard
    require_bot_admin: AdminGuard
    require_scope_admin: ScopeAdminGuard


def parse_on_off(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean: {raw}")


__all__ = ["RouterGuards", "parse_on_off"]
