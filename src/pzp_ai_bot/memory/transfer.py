from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from typing import Any, Literal

import ijson
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderSelection
from pzp_ai_bot.db.models import KnowledgeItem
from pzp_ai_bot.db.repos.embeddings import EmbeddingRepository
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.knowledge import KnowledgeRepository
from pzp_ai_bot.db.repos.messages import MessageRepository
from pzp_ai_bot.db.repos.settings import ScopeMetadataRepository, ScopeSettingsRepository
from pzp_ai_bot.db.repos.shared import utc_now
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.markdown import MarkdownMemoryStore, format_sources
from pzp_ai_bot.memory.scope import Scope

MemoryExportFormat = Literal["md", "jsonl"]
IMPORT_BATCH_SIZE = 100
EXPORT_QUERY_BATCH_SIZE = 250
EXPORT_TEXT_CHUNK_CHARS = 64 * 1024
MAX_IMPORT_ROWS = 100_000
MAX_IMPORT_FILE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MemoryExportChunk:
    text: str


@dataclass(frozen=True, slots=True)
class JsonlImportItem:
    line_number: int
    kind: str
    content: str
    source_message_ids: list[int]


@dataclass(frozen=True, slots=True)
class TelegramDesktopMessage:
    telegram_message_id: int
    user_id: int | None
    username: str | None
    text: str
    created_at: datetime
    reply_to_message_id: int | None
    chat_title: str | None


@dataclass(frozen=True, slots=True)
class TelegramDesktopImportResult:
    imported: int
    skipped: int


class MemoryTransferService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        markdown: MarkdownMemoryStore,
        default_scope_settings: Callable[[], Any],
        select_provider: Callable[[Any], ProviderSelection],
        generate_embeddings: Callable[..., Awaitable[Any]],
        add_embedding_usage_in_session: Callable[..., Awaitable[None]],
        compact_memory: Callable[..., Awaitable[Path]],
    ) -> None:
        self.session_factory = session_factory
        self.markdown = markdown
        self.default_scope_settings = default_scope_settings
        self.select_provider = select_provider
        self.generate_embeddings = generate_embeddings
        self.add_embedding_usage_in_session = add_embedding_usage_in_session
        self.compact_memory_callback = compact_memory

    async def export_memory(
        self, scope: Scope | None = None, fmt: MemoryExportFormat = "md"
    ) -> str:
        chunks = [chunk.text async for chunk in self.export_memory_chunks(scope=scope, fmt=fmt)]
        return "".join(chunks)

    async def export_memory_chunks(
        self, scope: Scope | None = None, fmt: MemoryExportFormat = "md"
    ) -> AsyncIterator[MemoryExportChunk]:
        if fmt not in {"md", "jsonl"}:
            raise ValueError("format must be md or jsonl")
        if fmt == "md" and scope is None:
            self.markdown.ensure()
            with self.markdown.main_path.open("r", encoding="utf-8") as handle:
                while text := handle.read(EXPORT_TEXT_CHUNK_CHARS):
                    yield MemoryExportChunk(text)
            return
        if fmt == "md":
            assert scope is not None
            async with session_scope(self.session_factory) as session:
                metadata = await ScopeMetadataRepository(session).get_effective(scope)
            yield MemoryExportChunk(f"# Memory export for {scope_label(scope, metadata)}\n")
            wrote_item = False
            after_id = 0
            while True:
                async with session_scope(self.session_factory) as session:
                    items = await KnowledgeRepository(session).list_page(
                        scope, after_id=after_id, limit=EXPORT_QUERY_BATCH_SIZE
                    )
                    if not items:
                        break
                    references = await MessageRepository(session).source_references(
                        scope,
                        [source for item in items for source in item.source_message_ids],
                        metadata,
                    )
                parts: list[str] = []
                for item in items:
                    if not wrote_item:
                        parts.append("\n")
                        wrote_item = True
                    sources = format_sources(item.source_message_ids, references)
                    extra = format_item_metadata(item.metadata_json or {})
                    detail = f"{item.kind}, source: {sources}{extra}"
                    parts.append(f"- ({detail}) {item.content.strip()}\n")
                for chunk in _memory_export_chunks(parts):
                    yield chunk
                after_id = int(items[-1].id)
            return

        after_id = 0
        while True:
            async with session_scope(self.session_factory) as session:
                items = await KnowledgeRepository(session).list_page(
                    scope, after_id=after_id, limit=EXPORT_QUERY_BATCH_SIZE
                )
                if not items:
                    break
                grouped: dict[Scope, list[KnowledgeItem]] = {}
                for item in items:
                    item_scope = Scope(item.chat_id, item.message_thread_id)
                    grouped.setdefault(item_scope, []).append(item)
                metadata_by_scope = {
                    item_scope: await ScopeMetadataRepository(session).get_effective(item_scope)
                    for item_scope in grouped
                }
                references_by_scope = {
                    item_scope: await MessageRepository(session).source_references(
                        item_scope,
                        [source for item in scope_items for source in item.source_message_ids],
                        metadata_by_scope[item_scope],
                    )
                    for item_scope, scope_items in grouped.items()
                }
            parts = []
            for item in items:
                item_scope = Scope(item.chat_id, item.message_thread_id)
                metadata = metadata_by_scope[item_scope]
                references = references_by_scope[item_scope]
                parts.append(
                    json.dumps(
                        {
                            "id": item.id,
                            "scope": item_scope.key,
                            "scope_label": scope_label(item_scope, metadata),
                            "kind": item.kind,
                            "content": item.content,
                            "source_message_ids": item.source_message_ids,
                            "metadata": item.metadata_json or {},
                            "source_references": [
                                {
                                    "message_id": references[source_id].message_id,
                                    "url": references[source_id].url,
                                }
                                for source_id in sorted(set(item.source_message_ids))
                                if source_id in references
                            ],
                            "created_at": item.created_at.isoformat(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            for chunk in _memory_export_chunks(parts):
                yield chunk
            after_id = int(items[-1].id)

    async def import_jsonl(self, path: Path, scope: Scope) -> int:
        imported = 0
        skipped = 0
        completed_batches = 0
        try:
            ensure_import_file_size(path)
            async with session_scope(self.session_factory) as session:
                scope_settings = await ScopeSettingsRepository(session).effective(
                    scope, self.default_scope_settings()
                )
            selection = self.select_provider(scope_settings)
            for batch_index, rows in enumerate(
                batched(iter_jsonl_import(path), IMPORT_BATCH_SIZE), start=1
            ):
                batch_rows = list(rows)
                seen_keys: set[tuple[str, str, tuple[int, ...]]] = set()
                unique_rows: list[JsonlImportItem] = []
                for row in batch_rows:
                    key = jsonl_import_key(row.kind, row.content, row.source_message_ids)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    unique_rows.append(row)
                async with session_scope(self.session_factory) as session:
                    existing = await KnowledgeRepository(session).list_exact_content(
                        scope, [row.content for row in unique_rows]
                    )
                existing_keys = {
                    jsonl_import_key(item.kind, item.content, item.source_message_ids)
                    for item in existing
                }
                candidates = [
                    row
                    for row in unique_rows
                    if jsonl_import_key(row.kind, row.content, row.source_message_ids)
                    not in existing_keys
                ]
                batch_skipped = len(batch_rows) - len(candidates)
                if not candidates:
                    async with session_scope(self.session_factory) as session:
                        await EventRepository(session).add(
                            event_type="jsonl_import_batch",
                            scope=scope,
                            payload={
                                "batch": batch_index,
                                "batch_size": len(batch_rows),
                                "imported": 0,
                                "skipped": batch_skipped,
                                "checkpoint_imported": imported,
                                "checkpoint_skipped": skipped + batch_skipped,
                                "first_line": batch_rows[0].line_number,
                                "last_line": batch_rows[-1].line_number,
                                "file": path.name,
                            },
                        )
                    skipped += batch_skipped
                    completed_batches = batch_index
                    continue
                embedding_generation = await self.generate_embeddings(
                    selection,
                    [row.content for row in candidates],
                    operation="embed_knowledge",
                    metadata={
                        "scope": scope.key,
                        "target_type": "knowledge_item",
                        "import": "jsonl",
                        "batch": batch_index,
                    },
                )
                if embedding_generation.error is not None:
                    async with session_scope(self.session_factory) as session:
                        await self.add_embedding_usage_in_session(
                            session,
                            selection=selection,
                            generation=embedding_generation,
                            texts_count=len(candidates),
                            operation="embed_knowledge",
                            metadata={
                                "scope": scope.key,
                                "target_type": "knowledge_item",
                                "import": "jsonl",
                                "batch": batch_index,
                            },
                        )
                    raise embedding_generation.error
                async with session_scope(self.session_factory) as session:
                    await self.add_embedding_usage_in_session(
                        session,
                        selection=selection,
                        generation=embedding_generation,
                        texts_count=len(candidates),
                        operation="embed_knowledge",
                        metadata={
                            "scope": scope.key,
                            "target_type": "knowledge_item",
                            "import": "jsonl",
                            "batch": batch_index,
                        },
                    )
                    knowledge_repo = KnowledgeRepository(session)
                    embedding_repo = EmbeddingRepository(session)
                    for vector_index, (row, vector) in enumerate(
                        zip(candidates, embedding_generation.vectors, strict=True)
                    ):
                        item = await knowledge_repo.add(
                            scope=scope,
                            kind=row.kind,
                            content=row.content,
                            source_message_ids=row.source_message_ids,
                        )
                        await embedding_repo.add(
                            target_type="knowledge_item",
                            target_id=item.id,
                            scope=scope,
                            provider=selection.embedding_provider.name,
                            model=selection.embedding_model,
                            vector=vector,
                            **embedding_generation.persistence_fields(vector_index),
                        )
                    await EventRepository(session).add(
                        event_type="jsonl_import_batch",
                        scope=scope,
                        payload={
                            "batch": batch_index,
                            "batch_size": len(batch_rows),
                            "imported": len(candidates),
                            "skipped": batch_skipped,
                            "checkpoint_imported": imported + len(candidates),
                            "checkpoint_skipped": skipped + batch_skipped,
                            "first_line": batch_rows[0].line_number,
                            "last_line": batch_rows[-1].line_number,
                            "file": path.name,
                        },
                    )
                imported += len(candidates)
                skipped += batch_skipped
                completed_batches = batch_index
        except Exception as exc:
            await self._record_import_failure(
                event_type="jsonl_import_failed",
                scope=scope,
                path=path,
                imported=imported,
                skipped=skipped,
                completed_batches=completed_batches,
                error=exc,
            )
            raise
        async with session_scope(self.session_factory) as session:
            await EventRepository(session).add(
                event_type="jsonl_import",
                scope=scope,
                payload={
                    "imported": imported,
                    "skipped": skipped,
                    "batches": completed_batches,
                    "batch_size": IMPORT_BATCH_SIZE,
                    "file": path.name,
                },
            )
        await self.compact_memory_callback(dirty_scopes={scope})
        return imported

    async def import_telegram_desktop_json(
        self, path: Path, scope: Scope
    ) -> TelegramDesktopImportResult:
        if scope.chat_id is None:
            raise ValueError("Telegram Desktop imports require a concrete chat or topic scope")
        imported = 0
        skipped = 0
        completed_batches = 0
        try:
            ensure_import_file_size(path)
            shape = detect_telegram_json_shape(path)
            async with session_scope(self.session_factory) as session:
                scope_settings = await ScopeSettingsRepository(session).effective(
                    scope, self.default_scope_settings()
                )
            selection = self.select_provider(scope_settings)
            for batch_index, rows in enumerate(
                batched(iter_telegram_json_import(path, shape), IMPORT_BATCH_SIZE), start=1
            ):
                batch_rows = list(rows)
                seen_ids: set[int] = set()
                candidates: list[TelegramDesktopMessage] = []
                for row in batch_rows:
                    if not row.text.strip() or row.telegram_message_id in seen_ids:
                        continue
                    seen_ids.add(row.telegram_message_id)
                    candidates.append(row)
                async with session_scope(self.session_factory) as session:
                    existing_ids = await MessageRepository(session).existing_logical_ids(
                        scope, [row.telegram_message_id for row in candidates]
                    )
                candidates = [
                    row for row in candidates if row.telegram_message_id not in existing_ids
                ]
                batch_skipped = len(batch_rows) - len(candidates)
                if not candidates:
                    async with session_scope(self.session_factory) as session:
                        await EventRepository(session).add(
                            event_type="telegram_json_import_batch",
                            scope=scope,
                            payload={
                                "batch": batch_index,
                                "imported": 0,
                                "skipped": batch_skipped,
                                "checkpoint_imported": imported,
                                "checkpoint_skipped": skipped + batch_skipped,
                                "file": path.name,
                            },
                        )
                    skipped += batch_skipped
                    completed_batches = batch_index
                    continue
                normalized_texts = [normalize_text(row.text) for row in candidates]
                embedding_generation = await self.generate_embeddings(
                    selection,
                    normalized_texts,
                    operation="embed_message",
                    metadata={
                        "scope": scope.key,
                        "target_type": "message",
                        "import": "telegram_json",
                        "batch": batch_index,
                    },
                )
                if embedding_generation.error is not None:
                    async with session_scope(self.session_factory) as session:
                        await self.add_embedding_usage_in_session(
                            session,
                            selection=selection,
                            generation=embedding_generation,
                            texts_count=len(candidates),
                            operation="embed_message",
                            metadata={
                                "scope": scope.key,
                                "target_type": "message",
                                "import": "telegram_json",
                                "batch": batch_index,
                            },
                        )
                    raise embedding_generation.error
                batch_imported = 0
                async with session_scope(self.session_factory) as session:
                    message_repo = MessageRepository(session)
                    embedding_repo = EmbeddingRepository(session)
                    await self.add_embedding_usage_in_session(
                        session,
                        selection=selection,
                        generation=embedding_generation,
                        texts_count=len(candidates),
                        operation="embed_message",
                        metadata={
                            "scope": scope.key,
                            "target_type": "message",
                            "import": "telegram_json",
                            "batch": batch_index,
                        },
                    )
                    for vector_index, (row, normalized_text, vector) in enumerate(
                        zip(candidates, normalized_texts, embedding_generation.vectors, strict=True)
                    ):
                        message, created = await message_repo.add(
                            telegram_message_id=row.telegram_message_id,
                            scope=scope,
                            user_id=row.user_id,
                            username=row.username,
                            text=row.text,
                            normalized_text=normalized_text,
                            learned=True,
                            skip_reason=None,
                            reply_to_message_id=row.reply_to_message_id,
                            created_at=row.created_at,
                            chat_title=row.chat_title,
                            chat_username=None,
                            topic_title=None,
                            message_permalink=None,
                        )
                        if not created:
                            batch_skipped += 1
                            continue
                        await embedding_repo.add(
                            target_type="message",
                            target_id=message.id,
                            scope=scope,
                            provider=selection.embedding_provider.name,
                            model=selection.embedding_model,
                            vector=vector,
                            **embedding_generation.persistence_fields(vector_index),
                        )
                        batch_imported += 1
                    await EventRepository(session).add(
                        event_type="telegram_json_import_batch",
                        scope=scope,
                        payload={
                            "batch": batch_index,
                            "imported": batch_imported,
                            "skipped": batch_skipped,
                            "checkpoint_imported": imported + batch_imported,
                            "checkpoint_skipped": skipped + batch_skipped,
                            "file": path.name,
                        },
                    )
                imported += batch_imported
                skipped += batch_skipped
                completed_batches = batch_index
        except Exception as exc:
            await self._record_import_failure(
                event_type="telegram_json_import_failed",
                scope=scope,
                path=path,
                imported=imported,
                skipped=skipped,
                completed_batches=completed_batches,
                error=exc,
            )
            raise
        async with session_scope(self.session_factory) as session:
            await EventRepository(session).add(
                event_type="telegram_json_import",
                scope=scope,
                payload={
                    "imported": imported,
                    "skipped": skipped,
                    "batches": completed_batches,
                    "batch_size": IMPORT_BATCH_SIZE,
                    "file": path.name,
                },
            )
        return TelegramDesktopImportResult(imported=imported, skipped=skipped)

    async def _record_import_failure(
        self,
        *,
        event_type: str,
        scope: Scope,
        path: Path,
        imported: int,
        skipped: int,
        completed_batches: int,
        error: Exception,
    ) -> None:
        async with session_scope(self.session_factory) as session:
            await EventRepository(session).add(
                event_type=event_type,
                scope=scope,
                payload={
                    "imported": imported,
                    "skipped": skipped,
                    "completed_batches": completed_batches,
                    "batch_size": IMPORT_BATCH_SIZE,
                    "file": path.name,
                    "error_type": type(error).__name__,
                },
            )


TelegramJsonShape = Literal["desktop", "result", "updates", "single_update"]
TELEGRAM_UPDATE_MESSAGE_KEYS = ("message", "edited_message", "channel_post", "edited_channel_post")


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def parse_memory_export_format(raw: str) -> MemoryExportFormat:
    if raw == "md":
        return "md"
    if raw == "jsonl":
        return "jsonl"
    raise ValueError("format must be md or jsonl")


def ensure_import_file_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_IMPORT_FILE_BYTES:
        raise ValueError(f"import file exceeds the {MAX_IMPORT_FILE_BYTES}-byte file limit")


def iter_jsonl_import(path: Path) -> Iterator[JsonlImportItem]:
    row_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed JSONL at line {line_number}: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"malformed JSONL at line {line_number}: row must be an object")
            content = str(payload.get("content") or payload.get("text") or "").strip()
            if not content:
                raise ValueError(f"malformed JSONL at line {line_number}: content is required")
            raw_source_ids = payload.get("source_message_ids", [])
            if not isinstance(raw_source_ids, list):
                raise ValueError(
                    f"malformed JSONL at line {line_number}: source_message_ids must be a list"
                )
            try:
                source_ids = [int(value) for value in raw_source_ids]
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"malformed JSONL at line {line_number}: source_message_ids must be integers"
                ) from exc
            row_count += 1
            if row_count > MAX_IMPORT_ROWS:
                raise ValueError(f"import file exceeds the {MAX_IMPORT_ROWS}-row limit")
            yield JsonlImportItem(
                line_number=line_number,
                kind=str(payload.get("kind") or "imported_note"),
                content=content,
                source_message_ids=source_ids,
            )
    if row_count == 0:
        raise ValueError("import file contains no memory items")


def jsonl_import_key(
    kind: str, content: str, source_message_ids: list[int]
) -> tuple[str, str, tuple[int, ...]]:
    return kind, content, tuple(sorted(set(source_message_ids)))


def iter_telegram_json_import(
    path: Path, shape: TelegramJsonShape
) -> Iterator[TelegramDesktopMessage]:
    if shape == "desktop":
        yield from _iter_telegram_desktop_messages(path)
        return
    yield from _iter_telegram_update_messages(path, shape)


def _iter_telegram_desktop_messages(path: Path) -> Iterator[TelegramDesktopMessage]:
    chat_title = _telegram_desktop_chat_title(path)
    yielded = False
    raw_count = 0
    try:
        with path.open("rb") as handle:
            for raw in ijson.items(handle, "messages.item"):
                raw_count += 1
                _validate_import_row_count(raw_count)
                if not isinstance(raw, dict) or raw.get("type") != "message":
                    continue
                yielded = True
                yield telegram_desktop_export_row(raw, chat_title)
    except ijson.JSONError as exc:
        raise ValueError(f"malformed Telegram JSON import: {_ijson_error_message(exc)}") from exc
    if not yielded:
        raise ValueError("Telegram Desktop export contains no importable messages")


def _telegram_desktop_chat_title(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            for value in ijson.items(handle, "name"):
                return str(value or "").strip() or None
    except ijson.JSONError as exc:
        raise ValueError(f"malformed Telegram JSON import: {_ijson_error_message(exc)}") from exc
    return None


def _iter_telegram_update_messages(
    path: Path, shape: TelegramJsonShape
) -> Iterator[TelegramDesktopMessage]:
    prefix = {"result": "result.item", "updates": "item", "single_update": ""}[shape]
    yielded = False
    raw_count = 0
    try:
        with path.open("rb") as handle:
            for candidate in ijson.items(handle, prefix):
                raw_count += 1
                _validate_import_row_count(raw_count)
                raw = telegram_update_candidate_message(candidate)
                if raw is None:
                    continue
                yielded = True
                yield telegram_update_row(raw)
    except ijson.JSONError as exc:
        raise ValueError(f"malformed Telegram JSON import: {_ijson_error_message(exc)}") from exc
    if not yielded:
        raise ValueError("Telegram update dump contains no importable messages")


def telegram_update_candidate_message(candidate: Any) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    if "message_id" in candidate:
        return candidate
    for key in TELEGRAM_UPDATE_MESSAGE_KEYS:
        raw_message = candidate.get(key)
        if isinstance(raw_message, dict):
            return raw_message
    return None


def _validate_import_row_count(row_count: int) -> None:
    if row_count > MAX_IMPORT_ROWS:
        raise ValueError(f"import file exceeds the {MAX_IMPORT_ROWS}-row limit")


def _ijson_error_message(error: ijson.JSONError) -> str:
    return str(error).strip().splitlines()[0] or "invalid JSON"


def _memory_export_chunks(parts: list[str]) -> Iterator[MemoryExportChunk]:
    buffer = ""
    for part in parts:
        buffer += part
        while len(buffer) >= EXPORT_TEXT_CHUNK_CHARS:
            yield MemoryExportChunk(buffer[:EXPORT_TEXT_CHUNK_CHARS])
            buffer = buffer[EXPORT_TEXT_CHUNK_CHARS:]
    if buffer:
        yield MemoryExportChunk(buffer)


def _unsupported_telegram_json_import() -> ValueError:
    return ValueError(
        "Telegram JSON import must be a Telegram Desktop export or Bot API update dump"
    )


def _detect_telegram_array_shape(path: Path) -> TelegramJsonShape:
    try:
        with path.open("rb") as handle:
            for prefix, event, _value in ijson.parse(handle):
                if prefix == "messages" and event == "start_array":
                    return "desktop"
                if prefix == "result" and event == "start_array":
                    return "result"
    except ijson.JSONError as exc:
        raise ValueError(f"malformed Telegram JSON import: {_ijson_error_message(exc)}") from exc
    raise _unsupported_telegram_json_import()


def detect_telegram_json_shape(path: Path) -> TelegramJsonShape:
    try:
        with path.open("rb") as handle:
            root_event = next(ijson.parse(handle), None)
    except ijson.JSONError as exc:
        raise ValueError(f"malformed Telegram JSON import: {_ijson_error_message(exc)}") from exc
    if root_event is None:
        raise _unsupported_telegram_json_import()
    prefix, event, _value = root_event
    if prefix == "" and event == "start_array":
        return "updates"
    if prefix != "" or event != "start_map":
        raise _unsupported_telegram_json_import()
    try:
        return _detect_telegram_array_shape(path)
    except ValueError as array_error:
        if "malformed Telegram JSON import" in str(array_error):
            raise
    try:
        with path.open("rb") as handle:
            for prefix, event, value in ijson.parse(handle):
                if (
                    prefix == ""
                    and event == "map_key"
                    and (value == "message_id" or value in TELEGRAM_UPDATE_MESSAGE_KEYS)
                ):
                    return "single_update"
    except ijson.JSONError as exc:
        raise ValueError(f"malformed Telegram JSON import: {_ijson_error_message(exc)}") from exc
    raise ValueError(
        "Telegram JSON import must be a Telegram Desktop export or Bot API update dump"
    )


def telegram_desktop_export_rows(payload: Any) -> list[TelegramDesktopMessage] | None:
    if not isinstance(payload, dict):
        return None
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    chat_title = str(payload.get("name") or "").strip() or None
    rows: list[TelegramDesktopMessage] = []
    for raw in messages:
        if not isinstance(raw, dict) or raw.get("type") != "message":
            continue
        rows.append(telegram_desktop_export_row(raw, chat_title))
    if not rows:
        raise ValueError("Telegram Desktop export contains no importable messages")
    return rows


def telegram_desktop_export_row(
    raw: dict[str, Any], chat_title: str | None
) -> TelegramDesktopMessage:
    try:
        message_id = int(raw["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Telegram Desktop message is missing a numeric id") from exc
    return TelegramDesktopMessage(
        telegram_message_id=message_id,
        user_id=telegram_desktop_user_id(raw.get("from_id")),
        username=telegram_desktop_username(raw.get("from")),
        text=telegram_desktop_message_text(raw),
        created_at=telegram_desktop_datetime(raw),
        reply_to_message_id=telegram_desktop_optional_int(raw.get("reply_to_message_id")),
        chat_title=chat_title,
    )


def telegram_update_dump_rows(payload: Any) -> list[TelegramDesktopMessage] | None:
    messages = telegram_update_message_payloads(payload)
    if messages is None:
        return None
    rows: list[TelegramDesktopMessage] = []
    for raw in messages:
        rows.append(telegram_update_row(raw))
    if not rows:
        raise ValueError("Telegram update dump contains no importable messages")
    return rows


def telegram_update_row(raw: dict[str, Any]) -> TelegramDesktopMessage:
    try:
        message_id = int(raw["message_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Telegram update message is missing a numeric message_id") from exc
    return TelegramDesktopMessage(
        telegram_message_id=message_id,
        user_id=telegram_update_user_id(raw),
        username=telegram_update_username(raw),
        text=telegram_update_message_text(raw),
        created_at=telegram_update_datetime(raw),
        reply_to_message_id=telegram_update_reply_to_message_id(raw),
        chat_title=telegram_update_chat_title(raw),
    )


def telegram_update_message_payloads(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("result"), list):
            candidates = payload["result"]
        elif "message_id" in payload or any(key in payload for key in TELEGRAM_UPDATE_MESSAGE_KEYS):
            candidates = [payload]
        else:
            return None
    elif isinstance(payload, list):
        candidates = payload
    else:
        return None
    messages: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if "message_id" in candidate:
            messages.append(candidate)
            continue
        for key in TELEGRAM_UPDATE_MESSAGE_KEYS:
            raw_message = candidate.get(key)
            if isinstance(raw_message, dict):
                messages.append(raw_message)
                break
    return messages


def telegram_desktop_message_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    content = telegram_desktop_text_value(row.get("text"))
    if content:
        parts.append(content)
    links = telegram_desktop_links(row)
    if links:
        parts.append("Links: " + ", ".join(links[:5]))
    attachment = telegram_desktop_attachment_label(row)
    if attachment:
        parts.append(attachment)
    return normalize_text("\n".join(parts))


def telegram_desktop_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks).strip()
    return ""


def telegram_desktop_links(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    entities = row.get("text_entities")
    if not isinstance(entities, list):
        return out
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        candidate = ""
        if entity.get("type") in {"link", "url"}:
            candidate = str(entity.get("text") or "")
        elif entity.get("type") == "text_link":
            candidate = str(entity.get("href") or entity.get("url") or "")
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def telegram_desktop_attachment_label(row: dict[str, Any]) -> str | None:
    file_name = telegram_desktop_filename(row.get("file") or row.get("photo"))
    media_type = str(row.get("media_type") or "").strip().lower()
    if file_name:
        label = "Photo" if media_type == "photo" or row.get("photo") else "Attachment"
        return f"{label}: {file_name}"
    if media_type == "photo" or row.get("photo"):
        return "Photo attachment"
    return None


def telegram_desktop_filename(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not clean or clean.startswith("("):
        return None
    return clean[:160]


def telegram_desktop_datetime(row: dict[str, Any]) -> datetime:
    raw_unix = row.get("date_unixtime")
    if raw_unix is not None:
        try:
            return datetime.fromtimestamp(int(raw_unix), tz=UTC)
        except (TypeError, ValueError):
            pass
    raw_date = str(row.get("date") or "").strip()
    if raw_date:
        try:
            parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return utc_now()


def telegram_desktop_user_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = "".join(char for char in value if char.isdigit())
        return int(digits) if digits else None
    return None


def telegram_desktop_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def telegram_desktop_username(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    return clean[:255] if clean else None


def telegram_update_message_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    content = str(row.get("text") or row.get("caption") or "").strip()
    if content:
        parts.append(content)
    links = telegram_update_links(row, content)
    if links:
        parts.append("Links: " + ", ".join(links[:5]))
    attachment = telegram_update_attachment_label(row)
    if attachment:
        parts.append(attachment)
    return normalize_text("\n".join(parts))


def telegram_update_links(row: dict[str, Any], content: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    entities = row.get("entities") if row.get("text") is not None else row.get("caption_entities")
    if not isinstance(entities, list):
        return out
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        candidate = ""
        entity_type = entity.get("type")
        if entity_type in {"url", "email"}:
            candidate = telegram_update_entity_slice(content, entity)
        elif entity_type == "text_link":
            candidate = str(entity.get("url") or "")
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def telegram_update_entity_slice(content: str, entity: dict[str, Any]) -> str:
    try:
        offset = int(entity.get("offset", 0))
        length = int(entity.get("length", 0))
    except (TypeError, ValueError):
        return ""
    if offset < 0 or length <= 0:
        return ""
    return content[offset : offset + length]


def telegram_update_attachment_label(row: dict[str, Any]) -> str | None:
    document = row.get("document")
    if isinstance(document, dict):
        file_name = telegram_desktop_filename(document.get("file_name"))
        if file_name:
            return f"Attachment: {file_name}"
    if isinstance(row.get("photo"), list) and row["photo"]:
        return "Photo attachment"
    return None


def telegram_update_datetime(row: dict[str, Any]) -> datetime:
    raw_date = row.get("date")
    if raw_date is None:
        return utc_now()
    try:
        return datetime.fromtimestamp(int(raw_date), tz=UTC)
    except (TypeError, ValueError):
        return utc_now()


def telegram_update_user_id(row: dict[str, Any]) -> int | None:
    user = row.get("from")
    if not isinstance(user, dict):
        return None
    return telegram_desktop_optional_int(user.get("id"))


def telegram_update_username(row: dict[str, Any]) -> str | None:
    user = row.get("from")
    if not isinstance(user, dict):
        return None
    username = str(user.get("username") or "").strip()
    if username:
        return username[:255]
    name = " ".join(
        part
        for part in [
            str(user.get("first_name") or "").strip(),
            str(user.get("last_name") or "").strip(),
        ]
        if part
    )
    return name[:255] if name else None


def telegram_update_reply_to_message_id(row: dict[str, Any]) -> int | None:
    reply = row.get("reply_to_message")
    if not isinstance(reply, dict):
        return None
    return telegram_desktop_optional_int(reply.get("message_id"))


def telegram_update_chat_title(row: dict[str, Any]) -> str | None:
    chat = row.get("chat")
    if not isinstance(chat, dict):
        return None
    title = str(chat.get("title") or chat.get("username") or "").strip()
    return title[:255] if title else None


def format_item_metadata(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    tags = metadata.get("tags")
    if isinstance(tags, list) and tags:
        parts.append("tags:" + ",".join(str(tag) for tag in tags))
    priority = metadata.get("priority")
    if priority is not None:
        parts.append(f"priority:{priority}")
    source = metadata.get("source")
    if source:
        parts.append(f"note-source:{source}")
    template = metadata.get("template")
    if template:
        parts.append(f"template:{template}")
    if not parts:
        return ""
    return ", " + ", ".join(parts)


def scope_label(scope: Scope, metadata: Any) -> str:
    if metadata is None:
        return f"`{scope.key}`"
    parts = [value for value in (metadata.chat_title, metadata.topic_title) if value]
    label = " / ".join(parts) or scope.key
    if metadata.chat_username:
        return f"{label} (`{scope.key}`, @{metadata.chat_username.removeprefix('@')})"
    return f"{label} (`{scope.key}`)"
