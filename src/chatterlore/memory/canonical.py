from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from chatterlore.db.models import KnowledgeItem
from chatterlore.memory.prompts import CANONICAL_PROMPT_VERSION

CATEGORY_HEADINGS = ("Facts", "Decisions", "Preferences", "Open Questions", "Todos")
CATEGORY_BY_NORMALIZED = {
    "facts": "Facts",
    "decisions": "Decisions",
    "preferences": "Preferences",
    "openquestions": "Open Questions",
    "todos": "Todos",
}
SOURCE_RE = re.compile(r"\[(?:source|sources):([^\]]+)\]", re.IGNORECASE)
ITEM_RE = re.compile(r"\[item:(\d+)(?:\s+sources:([^\]]*))?\]", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "to", "of", "and"}
PREFIXES = (
    "fact",
    "decision",
    "preference",
    "open question",
    "question",
    "todo",
    "task",
    "canonical note",
)


@dataclass(frozen=True, slots=True)
class CanonicalMemoryEntry:
    category: str
    content: str
    source_message_ids: list[int]
    item_ids: list[int]


def canonical_prompt(scope_key: str, items: list[KnowledgeItem]) -> str:
    lines = [
        f"Prompt version: {CANONICAL_PROMPT_VERSION}.",
        "Canonicalize durable group-chat memory.",
        "Group entries into Facts, Decisions, Preferences, Open Questions, and Todos.",
        "Deduplicate repeated facts and preserve item/source markers.",
        f"Scope: {scope_key}",
        "",
    ]
    for item in items:
        sources = ",".join(str(source) for source in item.source_message_ids) or "manual"
        compact = " ".join(item.content.strip().split())
        lines.append(f"- [item:{item.id} sources:{sources}] ({item.kind}) {compact}")
    return "\n".join(lines)


def parse_canonical_response(
    response: str, items_by_id: dict[int, KnowledgeItem]
) -> list[CanonicalMemoryEntry]:
    entries: list[CanonicalMemoryEntry] = []
    category = "Facts"
    for raw_line in response.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = line.strip("#:- ").strip()
        normalized_heading = normalize_category(heading)
        if normalized_heading in CATEGORY_BY_NORMALIZED:
            category = CATEGORY_BY_NORMALIZED[normalized_heading]
            continue
        if not line.startswith("-"):
            continue
        body = line.lstrip("- ").strip()
        item_ids = [int(match.group(1)) for match in ITEM_RE.finditer(body)]
        source_ids = parse_source_ids(body)
        for item_id in item_ids:
            item = items_by_id.get(item_id)
            if item is not None:
                source_ids.extend(int(value) for value in item.source_message_ids)
        content = clean_entry_content(body)
        if not content:
            continue
        entries.append(
            CanonicalMemoryEntry(
                category=category,
                content=content,
                source_message_ids=sorted(set(source_ids)),
                item_ids=item_ids,
            )
        )
    return deduplicate_entries(entries)


def fallback_entries(items: list[KnowledgeItem]) -> list[CanonicalMemoryEntry]:
    entries: list[CanonicalMemoryEntry] = []
    for item in items:
        source_ids = [int(value) for value in item.source_message_ids]
        for content in split_knowledge_content(item.content):
            combined_source_ids = sorted(set(source_ids + parse_source_ids(content)))
            clean = clean_entry_content(content)
            if not clean:
                continue
            entries.append(
                CanonicalMemoryEntry(
                    category=classify_entry(clean),
                    content=clean,
                    source_message_ids=combined_source_ids,
                    item_ids=[item.id],
                )
            )
    return deduplicate_entries(entries)


def canonical_entries_json(entries: Sequence[CanonicalMemoryEntry]) -> list[dict[str, Any]]:
    return [
        {
            "category": entry.category,
            "content": entry.content,
            "source_message_ids": list(entry.source_message_ids),
            "item_ids": list(entry.item_ids),
        }
        for entry in entries
    ]


def parse_canonical_entries_json(payload: Any) -> tuple[CanonicalMemoryEntry, ...] | None:
    if not isinstance(payload, list):
        return None
    entries: list[CanonicalMemoryEntry] = []
    for value in payload:
        if not isinstance(value, dict) or set(value) != {
            "category",
            "content",
            "source_message_ids",
            "item_ids",
        }:
            return None
        category = value["category"]
        content = value["content"]
        source_message_ids = value["source_message_ids"]
        item_ids = value["item_ids"]
        if (
            not isinstance(category, str)
            or category not in CATEGORY_HEADINGS
            or not isinstance(content, str)
            or not content.strip()
            or not _is_int_list(source_message_ids)
            or not _is_int_list(item_ids)
        ):
            return None
        entries.append(
            CanonicalMemoryEntry(
                category=category,
                content=content,
                source_message_ids=list(source_message_ids),
                item_ids=list(item_ids),
            )
        )
    return tuple(entries)


def canonical_scope_fingerprint(
    scope_key: str, items: list[KnowledgeItem], output_settings: dict[str, Any]
) -> str:
    payload = {
        "scope": scope_key,
        "items": [
            {
                "id": int(item.id),
                "kind": item.kind,
                "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
                "source_message_ids": sorted(int(value) for value in item.source_message_ids),
                "updated_at": _as_utc(item.updated_at).isoformat(timespec="microseconds"),
            }
            for item in sorted(items, key=lambda value: int(value.id))
        ],
        "output_settings": output_settings,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deduplicate_entries(entries: list[CanonicalMemoryEntry]) -> list[CanonicalMemoryEntry]:
    by_key: dict[tuple[str, str], CanonicalMemoryEntry] = {}
    order: list[tuple[str, str]] = []
    for entry in entries:
        fingerprint = semantic_fingerprint(entry.content)
        key = _matching_key(by_key, entry.category, fingerprint) or (entry.category, fingerprint)
        if key not in by_key:
            by_key[key] = entry
            order.append(key)
            continue
        current = by_key[key]
        by_key[key] = CanonicalMemoryEntry(
            category=current.category,
            content=_preferred_content(current.content, entry.content),
            source_message_ids=sorted(set(current.source_message_ids + entry.source_message_ids)),
            item_ids=sorted(set(current.item_ids + entry.item_ids)),
        )
    return [by_key[key] for key in order]


def split_knowledge_content(content: str) -> list[str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    bullets = [line.lstrip("- ").strip() for line in lines if line.startswith("-")]
    if bullets:
        return bullets
    return [" ".join(content.strip().split())]


def classify_entry(content: str) -> str:
    lower = content.lower().strip()
    if lower.startswith(("decision:", "decided", "we decided")) or " decision " in f" {lower} ":
        return "Decisions"
    if lower.startswith(("preference:", "prefers", "preference ")) or " prefers " in f" {lower} ":
        return "Preferences"
    if (
        lower.startswith(("todo:", "task:", "action:", "follow up"))
        or " needs to " in f" {lower} "
        or " should " in f" {lower} "
    ):
        return "Todos"
    if lower.startswith(("question:", "open question:")) or lower.endswith("?"):
        return "Open Questions"
    return "Facts"


def clean_entry_content(content: str) -> str:
    cleaned = ITEM_RE.sub("", content)
    cleaned = SOURCE_RE.sub("", cleaned)
    cleaned = re.sub(r"^\([^)]*\)\s*", "", cleaned).strip()
    cleaned = cleaned.lstrip("- ").strip()
    for prefix in PREFIXES:
        pattern = re.compile(rf"^{re.escape(prefix)}\s*:\s*", re.IGNORECASE)
        cleaned = pattern.sub("", cleaned)
    return " ".join(cleaned.split())


def parse_source_ids(content: str) -> list[int]:
    ids: list[int] = []
    for match in SOURCE_RE.finditer(content):
        ids.extend(_parse_source_values(match.group(1)))
    for match in ITEM_RE.finditer(content):
        if match.group(2):
            ids.extend(_parse_source_values(match.group(2)))
    return ids


def normalize_category(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def semantic_fingerprint(content: str) -> str:
    clean = clean_entry_content(content).lower()
    words = [word for word in WORD_RE.findall(clean) if word not in STOPWORDS]
    return " ".join(words)


def _matching_key(
    by_key: dict[tuple[str, str], CanonicalMemoryEntry], category: str, fingerprint: str
) -> tuple[str, str] | None:
    if not fingerprint:
        return None
    for existing_category, existing_fingerprint in by_key:
        if existing_category != category:
            continue
        if existing_fingerprint == fingerprint:
            return (existing_category, existing_fingerprint)
        if _fingerprints_overlap(existing_fingerprint, fingerprint):
            return (existing_category, existing_fingerprint)
    return None


def _fingerprints_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_words = set(left.split())
    right_words = set(right.split())
    if not left_words or not right_words:
        return False
    overlap = len(left_words & right_words) / min(len(left_words), len(right_words))
    return overlap >= 0.85


def _preferred_content(left: str, right: str) -> str:
    return left if len(left) <= len(right) else right


def _parse_source_values(raw: str) -> list[int]:
    ids: list[int] = []
    for value in re.split(r"[,\s]+", raw):
        value = value.strip("#` ")
        if not value or value == "manual":
            continue
        try:
            ids.append(int(value))
        except ValueError:
            continue
    return ids


def _is_int_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
