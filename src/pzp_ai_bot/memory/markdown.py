from __future__ import annotations

import re
import shutil
import tempfile
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, date, datetime
from pathlib import Path

from pzp_ai_bot.db.models import KnowledgeItem, ScopeMetadata
from pzp_ai_bot.db.repositories import SourceReference
from pzp_ai_bot.memory.canonical import CATEGORY_HEADINGS, CanonicalMemoryEntry, fallback_entries
from pzp_ai_bot.memory.scope import Scope

DAILY_SCOPE_FILE_RE = re.compile(r"(?:all|-?\d+)_(?:main|-?\d+)\.md")


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

    def rebuild_daily(self, items: list[KnowledgeItem]) -> list[Path]:
        self.ensure()
        memory_root = self.memory_dir.resolve()
        daily_root = self._contained_path(self.daily_dir, root=memory_root)
        staging_root = Path(
            tempfile.mkdtemp(prefix=".daily-staging-", dir=self.memory_dir)
        ).resolve()
        self._contained_path(staging_root, root=memory_root)
        expected: dict[Path, list[KnowledgeItem]] = defaultdict(list)
        for item in sorted(items, key=_daily_item_sort_key):
            created_at = _as_utc(item.created_at)
            relative_path = (
                Path(created_at.date().isoformat())
                / f"{Scope(item.chat_id, item.message_thread_id).filename_stem}.md"
            )
            expected[relative_path].append(item)
        try:
            for relative_path, path_items in expected.items():
                staging_path = self._contained_path(staging_root / relative_path, root=memory_root)
                staging_path.parent.mkdir(parents=True, exist_ok=True)
                staging_path.write_text(_daily_projection(path_items), encoding="utf-8")

            existing_paths = self._generated_daily_paths(daily_root, memory_root)
            written_paths: list[Path] = []
            expected_targets: set[Path] = set()
            for relative_path in sorted(expected, key=lambda value: value.as_posix()):
                staging_path = self._contained_path(staging_root / relative_path, root=memory_root)
                target_path = self._contained_path(daily_root / relative_path, root=memory_root)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                staging_path.replace(target_path)
                expected_targets.add(target_path)
                written_paths.append(target_path)

            for obsolete_path in sorted(
                existing_paths - expected_targets, key=lambda value: value.as_posix()
            ):
                self._contained_path(obsolete_path, root=memory_root).unlink()
                with suppress(OSError):
                    obsolete_path.parent.rmdir()
            return written_paths
        finally:
            self._contained_path(staging_root, root=memory_root)
            shutil.rmtree(staging_root, ignore_errors=True)

    @staticmethod
    def _contained_path(path: Path, *, root: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"daily Markdown path escapes memory directory: {path}")
        return resolved

    def _generated_daily_paths(self, daily_root: Path, memory_root: Path) -> set[Path]:
        paths: set[Path] = set()
        if not daily_root.exists():
            return paths
        for day_path in daily_root.iterdir():
            if not day_path.is_dir():
                continue
            try:
                date.fromisoformat(day_path.name)
            except ValueError:
                continue
            contained_day = self._contained_path(day_path, root=memory_root)
            for candidate in contained_day.iterdir():
                if candidate.is_file() and DAILY_SCOPE_FILE_RE.fullmatch(candidate.name):
                    paths.add(self._contained_path(candidate, root=memory_root))
        return paths

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


def _daily_projection(items: list[KnowledgeItem]) -> str:
    scope = Scope(items[0].chat_id, items[0].message_thread_id)
    lines = ["# Daily Memory Projection", "", f"Scope: `{scope.key}`", ""]
    for item in sorted(items, key=_daily_item_sort_key):
        sources = (
            ", ".join(str(value) for value in sorted(set(item.source_message_ids))) or "manual"
        )
        lines.extend(
            [
                f"## {_as_utc(item.created_at).isoformat(timespec='seconds')} [{item.kind}]",
                "",
                f"Sources: `{sources}`",
                "",
                item.content.strip(),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _daily_item_sort_key(item: KnowledgeItem) -> tuple[datetime, int]:
    return (_as_utc(item.created_at), int(item.id or 0))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
