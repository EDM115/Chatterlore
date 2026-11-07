from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_MESSAGE_ID = re.compile(r"message(-?\d+)\Z")
_REPLY_ID = re.compile(r"(?:messages\d*\.html)?#go_to_message(-?\d+)\Z")
_PAGE_NAME = re.compile(r"messages(?:[1-9]\d*)?\.html\Z")


@dataclass(frozen=True, slots=True)
class TelegramHtmlMessage:
    telegram_message_id: int
    username: str | None
    text: str
    created_at: datetime
    reply_to_message_id: int | None
    chat_title: str | None


@dataclass(slots=True)
class _Frame:
    tag: str
    classes: frozenset[str]
    pagination_href: str | None = None
    pagination_text: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Message:
    message_id: int
    service: bool
    joined: bool
    depth: int
    date: str = ""
    author: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    attachment: list[str] = field(default_factory=list)
    attachment_path: str | None = None
    links: list[str] = field(default_factory=list)
    reply_id: int | None = None


def _clean(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class _TelegramHtmlParser(HTMLParser):
    def __init__(
        self, *, last_author: str | None, expected_title: str | None, max_rows: int
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.frames: list[_Frame] = []
        self.message: _Message | None = None
        self.rows: deque[TelegramHtmlMessage] = deque()
        self.next_pages: list[str] = []
        self.header: list[str] = []
        self.last_author = last_author
        self.expected_title = expected_title
        self.max_rows = max_rows
        self.row_count = 0
        self.chat_page = False

    @property
    def chat_title(self) -> str | None:
        return _clean(self.header) or None

    def _inside(self, *classes: str) -> bool:
        return any(frame.classes.intersection(classes) for frame in self.frames)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = frozenset((values.get("class") or "").split())
        if "chat_page" in classes:
            self.chat_page = True
        if "message" in classes:
            if not self.chat_page or not self.chat_title:
                raise ValueError("malformed Telegram HTML: missing export header")
            if self.message is not None:
                raise ValueError("malformed Telegram HTML: nested message blocks")
            match = _MESSAGE_ID.fullmatch(values.get("id") or "")
            if match is None:
                raise ValueError("Telegram HTML message is missing a numeric id")
            self.row_count += 1
            if self.row_count > self.max_rows:
                raise ValueError("Telegram HTML import exceeds the row limit")
            if self.expected_title is not None and self.chat_title != self.expected_title:
                raise ValueError("Telegram HTML pagination changes the chat title")
            self.message = _Message(
                int(match[1]), "service" in classes, "joined" in classes, len(self.frames)
            )
        row = self.message
        if row is not None:
            if "date" in classes and not row.date and not self._inside("forwarded"):
                row.date = values.get("title") or ""
            if tag == "br" and self._inside("text"):
                row.text.append(" ")
            if tag == "a":
                href = values.get("href") or ""
                if self._inside("reply_to"):
                    if match := _REPLY_ID.fullmatch(href):
                        row.reply_id = int(match[1])
                elif self._inside("text"):
                    if urlsplit(href).scheme in {"https", "http"} and href not in row.links:
                        row.links.append(href)
                elif self._inside("media_wrap") and href and not urlsplit(href).scheme:
                    row.attachment_path = (
                        unquote(urlsplit(href).path).replace("\\", "/").rsplit("/", 1)[-1]
                    )
        if tag not in _VOID_TAGS:
            href = (
                values.get("href")
                if tag == "a" and "pagination" in classes and row is None
                else None
            )
            self.frames.append(_Frame(tag, classes, href))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if not self.frames or self.frames[-1].tag != tag:
            raise ValueError("malformed Telegram HTML: unmatched closing tag")
        frame = self.frames.pop()
        if (
            frame.pagination_href is not None
            and "previous" not in _clean(frame.pagination_text).casefold()
        ):
            self.next_pages.append(frame.pagination_href)
        if self.message is not None and self.message.depth == len(self.frames):
            self._finish_message()

    def handle_data(self, data: str) -> None:
        if any(frame.tag in {"script", "style"} for frame in self.frames):
            return
        row = self.message
        if row is None:
            if self._inside("page_header") and self._inside("text"):
                self.header.append(data)
            for frame in self.frames:
                if frame.pagination_href is not None:
                    frame.pagination_text.append(data)
            return
        if row.service:
            return
        if self._inside("from_name") and not self._inside("forwarded"):
            row.author.append(data)
        elif self._inside("text"):
            row.text.append(data)
        elif self._inside("title") and self._inside("media_wrap", "media_file"):
            row.attachment.append(data)

    def _finish_message(self) -> None:
        row = self.message
        self.message = None
        assert row is not None
        if row.service:
            return
        try:
            created_at = datetime.strptime(row.date, "%d.%m.%Y %H:%M:%S UTC%z")
        except ValueError as exc:
            raise ValueError(
                "Telegram HTML message is missing a valid timezone-qualified timestamp"
            ) from exc
        author = _clean(row.author) or (self.last_author if row.joined else None)
        self.last_author = author
        parts = [_clean(row.text)]
        if row.links:
            parts.append("Links: " + ", ".join(row.links[:5]))
        attachment = _clean(row.attachment) or row.attachment_path
        if attachment:
            parts.append("Attachment: " + attachment)
        self.rows.append(
            TelegramHtmlMessage(
                row.message_id,
                author,
                " ".join(part for part in parts if part),
                created_at,
                row.reply_id,
                self.chat_title,
            )
        )


def _next_html_page(root: Path, href: str) -> Path:
    if _PAGE_NAME.fullmatch(href) is None:
        raise ValueError("unsafe Telegram HTML pagination path")
    page = root / href
    if page.is_symlink() or page.resolve().parent != root:
        raise ValueError("unsafe Telegram HTML pagination link")
    if not page.is_file():
        raise ValueError("Telegram HTML pagination page is missing")
    return page


def iter_telegram_html(
    path: Path, *, max_rows: int = 100_000, max_bytes: int = 512 * 1024 * 1024
) -> Iterator[TelegramHtmlMessage]:
    if max_rows <= 0 or max_bytes <= 0:
        raise ValueError("Telegram HTML import limits must be positive")
    if path.is_symlink():
        raise ValueError("unsafe Telegram HTML pagination link")
    root = path.parent.resolve()
    page: Path | None = path.resolve()
    visited: set[Path] = set()
    row_count = 0
    total_bytes = 0
    last_author: str | None = None
    chat_title: str | None = None
    yielded = False
    while page is not None:
        if page in visited or len(visited) >= 1000:
            raise ValueError("Telegram HTML pagination cycle or page limit")
        visited.add(page)
        if total_bytes + page.stat().st_size > max_bytes:
            raise ValueError("Telegram HTML import exceeds the byte limit")
        parser = _TelegramHtmlParser(
            last_author=last_author, expected_title=chat_title, max_rows=max_rows - row_count
        )
        try:
            with page.open("r", encoding="utf-8-sig") as handle:
                while chunk := handle.read(64 * 1024):
                    total_bytes += len(chunk.encode("utf-8"))
                    if total_bytes > max_bytes:
                        raise ValueError("Telegram HTML import exceeds the byte limit")
                    parser.feed(chunk)
                    while parser.rows:
                        yielded = True
                        yield parser.rows.popleft()
            parser.close()
        except UnicodeError as exc:
            raise ValueError("Telegram HTML export must be valid UTF-8") from exc
        if parser.message is not None or parser.frames:
            raise ValueError("truncated or malformed Telegram HTML export")
        if not parser.chat_page or not parser.chat_title:
            raise ValueError("file is not a Telegram Desktop HTML export")
        row_count += parser.row_count
        last_author = parser.last_author
        chat_title = parser.chat_title
        pages = list(dict.fromkeys(parser.next_pages))
        if len(pages) > 1:
            raise ValueError("ambiguous Telegram HTML pagination")
        page = _next_html_page(root, pages[0]) if pages else None
    if not yielded:
        raise ValueError("Telegram HTML export contains no importable messages")
