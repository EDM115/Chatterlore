from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from pzp_ai_bot.db.models import KnowledgeItem
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
        self.ensure()
        grouped: dict[str, list[KnowledgeItem]] = defaultdict(list)
        for item in items:
            scope = Scope(item.chat_id, item.message_thread_id)
            grouped[scope.key].append(item)
        lines = [
            "# Memory",
            "",
            f"Last compacted: {datetime.now(UTC).isoformat(timespec='seconds')}",
            "",
        ]
        if not grouped:
            lines.append("No durable memory has been learned yet.")
        for scope_key in sorted(grouped):
            lines.extend([f"## Scope `{scope_key}`", ""])
            for item in sorted(grouped[scope_key], key=lambda value: value.created_at):
                sources = ", ".join(str(source) for source in item.source_message_ids) or "manual"
                compact = " ".join(item.content.strip().split())
                lines.append(f"- ({item.kind}, sources: {sources}) {compact}")
            lines.append("")
        self.main_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return self.main_path

    def read_main(self) -> str:
        self.ensure()
        return self.main_path.read_text(encoding="utf-8")
