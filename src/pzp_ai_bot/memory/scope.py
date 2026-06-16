from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Scope:
    chat_id: int | None
    thread_id: int | None = None

    @classmethod
    def global_scope(cls) -> Scope:
        return cls(chat_id=None, thread_id=None)

    @property
    def is_global(self) -> bool:
        return self.chat_id is None

    @property
    def key(self) -> str:
        chat = "all" if self.chat_id is None else str(self.chat_id)
        thread = "main" if self.thread_id is None else str(self.thread_id)
        return f"{chat}:{thread}"

    @property
    def filename_stem(self) -> str:
        chat = "all" if self.chat_id is None else str(self.chat_id)
        thread = "main" if self.thread_id is None else str(self.thread_id)
        return f"{chat}_{thread}"

    def contains(self, other: Scope) -> bool:
        if self.chat_id is None:
            return True
        if self.chat_id != other.chat_id:
            return False
        if self.thread_id is None:
            return True
        return self.thread_id == other.thread_id


def parse_scope(raw: str | None, *, current: Scope | None = None) -> Scope:
    if raw is None or raw == "" or raw == "current":
        if current is None:
            raise ValueError("current scope is not available")
        return current
    if raw == "all":
        return Scope.global_scope()
    if raw == "this_chat":
        if current is None or current.chat_id is None:
            raise ValueError("this_chat scope is not available")
        return Scope(current.chat_id, None)
    if raw == "this_topic":
        if current is None or current.chat_id is None:
            raise ValueError("this_topic scope is not available")
        return current
    parts = raw.split(":")
    if len(parts) == 1:
        return Scope(int(parts[0]), None)
    if len(parts) == 2:
        thread = None if parts[1] in {"", "main", "none"} else int(parts[1])
        return Scope(int(parts[0]), thread)
    raise ValueError(f"invalid scope: {raw}")
