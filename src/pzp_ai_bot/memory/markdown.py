from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from pzp_ai_bot.db.models import KnowledgeItem, ScopeMetadata
from pzp_ai_bot.db.repositories import SourceReference
from pzp_ai_bot.memory.canonical import CATEGORY_HEADINGS, CanonicalMemoryEntry, fallback_entries
from pzp_ai_bot.memory.scope import Scope


class MarkdownMemoryStore:
    def __init__(self, memory_dir: Path) -> None:
        self.memory_dir = memory_dir
        self.daily_dir = memory_dir / "daily"
        self.main_path = memory_dir / "MEMORY.md"

    def ensure(self) -> None:
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        if not self.main_path.exists():
            self.main_path.parent.mkdir(parents=True, exist_ok=True)
            self.main_path.write_text("# Memory\n\nNo compacted memory yet.\n", encoding="utf-8")

    def append_daily(
        self,
        *,
        scope: Scope,
        content: str,
        source_message_ids: list[int],
        day: date | None = None,
        kind: str = "summary",
    ) -> Path:
        self.ensure()
        day = day or datetime.now(UTC).date()
        target_dir = self.daily_dir / day.isoformat()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{scope.filename_stem}.md"
        sources = ", ".join(str(item) for item in source_message_ids) or "manual"
        block = (
            f"\n## {datetime.now(UTC).isoformat(timespec='seconds')} [{kind}]\n\n"
            f"Scope: `{scope.key}`\n\n"
            f"Sources: `{sources}`\n\n"
            f"{content.strip()}\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
        return path

    def compact_main(self, items: list[KnowledgeItem]) -> Path:
        grouped: dict[Scope, list[CanonicalMemoryEntry]] = defaultdict(list)
        for item in items:
            scope = Scope(item.chat_id, item.message_thread_id)
            grouped[scope].extend(fallback_entries([item]))
        return self.write_canonical(grouped, {}, {})

    def write_canonical(
        self,
        entries_by_scope: dict[Scope, list[CanonicalMemoryEntry]],
        metadata_by_scope: dict[str, ScopeMetadata],
        sources_by_scope: dict[str, dict[int, SourceReference]],
    ) -> Path:
        self.ensure()
        lines = [
            "# Memory",
            "",
            f"Last compacted: {datetime.now(UTC).isoformat(timespec='seconds')}",
            "",
        ]
        nonempty_scopes = {scope: entries for scope, entries in entries_by_scope.items() if entries}
        if not nonempty_scopes:
            lines.append("No durable memory has been learned yet.")
        for scope in sorted(nonempty_scopes, key=lambda value: value.key):
            metadata = metadata_by_scope.get(scope.key)
            lines.extend([f"## {format_scope_heading(scope, metadata)}", ""])
            entries = nonempty_scopes[scope]
            for category in CATEGORY_HEADINGS:
                category_entries = [entry for entry in entries if entry.category == category]
                if not category_entries:
                    continue
                lines.extend([f"### {category}", ""])
                for entry in category_entries:
                    source_text = format_sources(
                        entry.source_message_ids, sources_by_scope.get(scope.key, {})
                    )
                    lines.append(f"- {entry.content} (source: {source_text})")
                lines.append("")
        self.main_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return self.main_path

    def read_main(self) -> str:
        self.ensure()
        return self.main_path.read_text(encoding="utf-8")


def format_scope_heading(scope: Scope, metadata: ScopeMetadata | None) -> str:
    if metadata is None:
        return f"Scope `{scope.key}`"
    parts: list[str] = []
    if metadata.chat_title:
        parts.append(metadata.chat_title)
    if metadata.topic_title:
        parts.append(metadata.topic_title)
    label = " / ".join(parts) or scope.key
    if metadata.chat_username:
        return f"{label} (`{scope.key}`, @{metadata.chat_username.removeprefix('@')})"
    return f"{label} (`{scope.key}`)"


def format_sources(source_message_ids: list[int], references: dict[int, SourceReference]) -> str:
    if not source_message_ids:
        return "manual"
    formatted: list[str] = []
    for source_id in sorted(set(source_message_ids)):
        reference = references.get(source_id)
        label = reference.label if reference else f"#{source_id}"
        if reference and reference.url:
            formatted.append(f"[{label}]({reference.url})")
        else:
            formatted.append(label)
    return ", ".join(formatted)
