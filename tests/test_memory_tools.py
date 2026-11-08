from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from chatterlore.ai.fake import FakeProvider
from chatterlore.ai.protocols import ChatMessage, ChatResult
from chatterlore.memory.scope import Scope
from chatterlore.memory.service import IncomingMessage


class RecordingFakeProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        self.prompts.append("\n".join(message.content for message in messages))
        return await super().generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )


def incoming(
    scope: Scope, text: str, *, message_id: int, created_at: datetime | None = None
) -> IncomingMessage:
    return IncomingMessage(
        telegram_message_id=message_id,
        scope=scope,
        user_id=222,
        username="dana",
        text=text,
        created_at=created_at or datetime(2026, 6, 23, tzinfo=UTC),
    )


async def test_manual_notes_support_tags_priority_source_edit_delete_and_export(context) -> None:
    scope = Scope(-100410, None)

    first = await context.memory.remember(
        scope,
        "Dana owns release coordination.",
        source_message_id=10,
        tags=["release", "owner"],
        priority=2,
        source="planning-doc",
    )
    second = await context.memory.remember(scope, "Obsolete note.", tags=["obsolete"])

    notes = await context.memory.list_notes(scope, tag="release")
    assert [note.id for note in notes] == [first]
    assert notes[0].tags == ["release", "owner"]
    assert notes[0].priority == 2
    assert notes[0].source == "planning-doc"

    edited = await context.memory.edit_note(
        scope,
        first,
        content="Dana owns beta release coordination.",
        tags=["beta"],
        priority=1,
        source="roadmap",
    )
    assert edited.tags == ["beta"]
    assert edited.priority == 1
    assert edited.source == "roadmap"

    exported = await context.memory.export_memory(scope, fmt="jsonl")
    assert '"tags": ["beta"]' in exported
    assert '"priority": 1' in exported
    assert '"source": "roadmap"' in exported

    await context.memory.delete_note(scope, second)
    all_notes = await context.memory.list_notes(scope)
    assert [note.id for note in all_notes] == [first]


async def test_cross_chat_profile_requires_query_permission_and_enabled_source_policy(
    context,
) -> None:
    destination = Scope(-100500, None)
    source = Scope(-100501, 7)
    await context.memory.set_learning(destination, True, updated_by=111)
    await context.memory.set_learning(source, True, updated_by=111)
    await context.memory.remember(
        source, "The shared launch codename is Quartz.", source_message_id=50
    )
    await context.memory.set_memory_profile("launch", [source], updated_by=111)

    with pytest.raises(ValueError, match="no permitted"):
        await context.memory.search(destination, "Quartz codename", profile_name="launch")

    await context.memory.allow_memory_profile(
        destination=destination, profile_name="launch", can_query=True, updated_by=111
    )
    results = await context.memory.search(destination, "Quartz codename", profile_name="launch")
    assert any(result.scope == source and "Quartz" in result.content for result in results)

    await context.memory.set_learning(source, False, updated_by=111)
    with pytest.raises(ValueError, match="no permitted"):
        await context.memory.search(destination, "Quartz codename", profile_name="launch")


async def test_summary_templates_apply_to_scope_and_range_summaries(context) -> None:
    recorder = RecordingFakeProvider()
    context.memory.chat_provider = recorder
    await context.memory.set_summary_template(
        "handoff", "Extract decisions and handoff todos only.", updated_by=111
    )

    scope_summary = Scope(-100601, None)
    await context.memory.set_learning(scope_summary, True, updated_by=111)
    await context.memory.set_scope_settings(
        scope_summary, {"summary_batch_size": 10}, updated_by=111
    )
    await context.memory.ingest(
        incoming(scope_summary, "Decision: publish alpha docs.", message_id=11)
    )
    await context.memory.ingest(
        incoming(scope_summary, "Todo: Dana should send the handoff note.", message_id=12)
    )
    assert await context.memory.summarize_now(scope_summary, template_name="handoff") == 2
    assert any(
        "Scope summary from pending learned messages." in prompt
        and "Extract decisions and handoff todos only." in prompt
        for prompt in recorder.prompts
    )

    range_scope = Scope(-100600, 3)
    await context.memory.set_learning(range_scope, True, updated_by=111)
    start = datetime(2026, 6, 23, 8, tzinfo=UTC)
    await context.memory.ingest(
        incoming(range_scope, "Decision: keep SQLite for alpha.", message_id=1, created_at=start)
    )
    await context.memory.ingest(
        incoming(
            range_scope,
            "Todo: Dana should publish the runbook.",
            message_id=2,
            created_at=start + timedelta(minutes=1),
        )
    )
    await context.memory.ingest(
        incoming(
            range_scope,
            "Outside range fact.",
            message_id=3,
            created_at=start + timedelta(minutes=10),
        )
    )

    result = await context.memory.summarize_range(
        range_scope, start_message_id=1, end_message_id=2, template_name="handoff"
    )

    assert result.template_name == "handoff"
    assert result.source_message_ids == [1, 2]
    assert any(
        "Range: Telegram messages 1..2" in prompt
        and "Extract decisions and handoff todos only." in prompt
        for prompt in recorder.prompts
    )
    exported = await context.memory.export_memory(range_scope, fmt="jsonl")
    assert '"kind": "range_summary"' in exported
    assert '"template": "handoff"' in exported
    assert '"start_message_id": 1' in exported
    assert "Outside range fact" not in exported
