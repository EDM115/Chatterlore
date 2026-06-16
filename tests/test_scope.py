from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from pzp_ai_bot.memory.scope import Scope, parse_scope
from pzp_ai_bot.telegram.utils import message_created_at, scope_from_message


def test_topic_scope_uses_thread_id_only_for_topic_messages() -> None:
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100), is_topic_message=True, message_thread_id=42
    )
    assert scope_from_message(message) == Scope(-100, 42)

    non_topic = SimpleNamespace(
        chat=SimpleNamespace(id=-100), is_topic_message=False, message_thread_id=42
    )
    assert scope_from_message(non_topic) == Scope(-100, None)


def test_parse_scope_variants() -> None:
    current = Scope(-100, 42)
    assert parse_scope("this_topic", current=current) == Scope(-100, 42)
    assert parse_scope("this_chat", current=current) == Scope(-100, None)
    assert parse_scope("all", current=current).is_global
    assert parse_scope("-100:42") == Scope(-100, 42)


def test_message_created_at_adds_timezone() -> None:
    raw = datetime(2026, 6, 16, 12, 0)
    message = SimpleNamespace(date=raw)
    assert message_created_at(message).tzinfo == UTC
