# ruff: noqa: E501

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def html_message(
    message_id: int,
    text: str,
    *,
    author: str | None = "Dana Example",
    joined: bool = False,
    date: str = "07.11.2026 19:18:44 UTC+01:00",
    extra: str = "",
) -> str:
    name = f'<div class="from_name">{author}</div>' if author is not None else ""
    classes = "message default clearfix" + (" joined" if joined else "")
    return f'<div class="{classes}" id="message{message_id}"><div class="body"><div class="pull_right date details" title="{date}">19:18</div>{name}{extra}<div class="text">{text}</div></div></div>'


def write_html(path: Path, body: str, *, pagination: str = "") -> Path:
    path.write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8"/></head><body><div class="page_wrap"><div class="page_header"><div class="content"><div class="text bold">Fixture Group</div></div></div><div class="page_body chat_page"><div class="history">'
        + body
        + "</div>"
        + pagination
        + "</div></div></body></html>",
        encoding="utf-8",
    )
    return path


def read_rows(path: Path, **limits):
    from chatterlore.memory.telegram_html import iter_telegram_html

    return list(iter_telegram_html(path, **limits))


def test_html_reader_preserves_message_content_and_ignores_active_content(tmp_path: Path) -> None:
    path = write_html(
        tmp_path / "messages.html",
        '<div class="message service" id="message3"><div class="body details">Dana created topic &laquo;News&raquo;</div></div>'
        + html_message(
            4,
            'Read <b>Tom &amp; Dana</b><br/><a href="https://example.test/runbook?a=1&amp;b=2">the runbook</a><script>DO NOT IMPORT THIS</script><style>NEITHER THIS</style>',
            extra='<div class="reply_to details">In reply to <a href="#go_to_message3">this message</a></div>',
        ),
    )

    [row] = read_rows(path)

    assert row.telegram_message_id == 4
    assert row.username == "Dana Example"
    assert row.chat_title == "Fixture Group"
    assert row.reply_to_message_id == 3
    assert row.created_at == datetime(2026, 11, 7, 19, 18, 44, tzinfo=timezone(timedelta(hours=1)))
    assert row.text == "Read Tom & Dana the runbook Links: https://example.test/runbook?a=1&b=2"


def test_html_reader_keeps_joined_author_without_inheriting_forwarded_sender(
    tmp_path: Path,
) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(1, "First")
        + html_message(
            2,
            "Second",
            author=None,
            joined=True,
            extra='<div class="forwarded body"><div class="from_name">Forwarded Person</div></div>',
        )
        + html_message(3, "Unknown sender", author=None),
    )

    rows = read_rows(path)

    assert [(row.telegram_message_id, row.username) for row in rows] == [
        (1, "Dana Example"),
        (2, "Dana Example"),
        (3, None),
    ]


def test_html_reader_preserves_attachment_labels_without_opening_files(tmp_path: Path) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(
            7,
            "Deployment attachment",
            extra='<div class="media_wrap clearfix"><a class="media clearfix pull_left media_file" href="files/runbook.pdf"><div class="body"><div class="title bold">runbook.pdf</div><div class="description">Not included</div></div></a></div>',
        ),
    )

    [row] = read_rows(path)

    assert row.text == "Deployment attachment Attachment: runbook.pdf"


def test_html_reader_follows_local_next_page_and_carries_joined_author(tmp_path: Path) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(1, "First"),
        pagination='<a class="pagination block_link" href="messages2.html">Next messages</a>',
    )
    write_html(
        tmp_path / "messages2.html",
        html_message(2, "Second", joined=True, author=None),
        pagination='<a class="pagination block_link" href="messages.html">Previous messages</a>',
    )

    rows = read_rows(path)

    assert [(row.telegram_message_id, row.username) for row in rows] == [
        (1, "Dana Example"),
        (2, "Dana Example"),
    ]


@pytest.mark.parametrize(
    "href",
    [
        "../messages2.html",
        "https://example.test/messages2.html",
        "file:///messages2.html",
        "messages2.html?x=1",
        "other.html",
    ],
)
def test_html_reader_rejects_unsafe_pagination(tmp_path: Path, href: str) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(1, "First"),
        pagination=f'<a class="pagination block_link" href="{href}">Next messages</a>',
    )

    with pytest.raises(ValueError, match="pagination"):
        read_rows(path)


def test_html_reader_rejects_pagination_cycles_and_global_limits(tmp_path: Path) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(1, "First"),
        pagination='<a class="pagination block_link" href="messages2.html">Next messages</a>',
    )
    write_html(
        tmp_path / "messages2.html",
        html_message(2, "Second"),
        pagination='<a class="pagination block_link" href="messages.html">Next messages</a>',
    )

    with pytest.raises(ValueError, match="pagination"):
        read_rows(path)
    with pytest.raises(ValueError, match="row limit"):
        read_rows(path, max_rows=1)
    with pytest.raises(ValueError, match=r"byte.*limit"):
        read_rows(path, max_bytes=path.stat().st_size + 10)


@pytest.mark.parametrize("date", ["", "yesterday", "07.11.2026 19:18:44"])
def test_html_reader_rejects_missing_or_ambiguous_timezone_dates(tmp_path: Path, date: str) -> None:
    path = write_html(tmp_path / "messages.html", html_message(1, "First", date=date))

    with pytest.raises(ValueError, match="timestamp"):
        read_rows(path)


def test_html_reader_rejects_truncated_messages_and_unrelated_html(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.html"
    truncated.write_text(
        '<div class="message default" id="message1"><div class="text">unfinished', encoding="utf-8"
    )
    unrelated = tmp_path / "unrelated.html"
    unrelated.write_text("<html><body><p>Not an export</p></body></html>", encoding="utf-8")

    with pytest.raises(ValueError, match=r"truncated|malformed"):
        read_rows(truncated)
    with pytest.raises(ValueError, match=r"Telegram.*HTML|importable"):
        read_rows(unrelated)


def test_html_reader_does_not_yield_unbranded_message_blocks(tmp_path: Path) -> None:
    from chatterlore.memory.telegram_html import iter_telegram_html

    path = tmp_path / "not-an-export.html"
    path.write_text(
        "<html><body>" + html_message(1, "Not an archive") + "</body></html>", encoding="utf-8"
    )

    with pytest.raises(ValueError, match=r"Telegram.*HTML"):
        next(iter_telegram_html(path))


def test_html_reader_rejects_symlink_pagination(tmp_path: Path) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(1, "First"),
        pagination='<a class="pagination block_link" href="messages2.html">Next messages</a>',
    )
    outside = tmp_path.parent / "outside-pagination.html"
    write_html(outside, html_message(2, "Outside file must not be read"))
    (tmp_path / "messages2.html").symlink_to(outside)

    with pytest.raises(ValueError, match="pagination"):
        read_rows(path)


def test_html_reader_preserves_forwarded_body_breaks_links_and_attachment_paths(
    tmp_path: Path,
) -> None:
    path = write_html(
        tmp_path / "messages.html",
        html_message(
            9,
            "",
            author="Actual Sender",
            extra='<div class="forwarded body"><div class="from_name">Original Author</div><div class="date details" title="01.01.2000 00:00:00 UTC+00:00">original date</div><div class="text">First<br>Second <a href="https://example.test/runbook">runbook</a></div><div class="media_wrap"><a class="media_file" href="files/replay.txt">attachment</a></div></div>',
        ),
    )

    [row] = read_rows(path)

    assert row.username == "Actual Sender"
    assert row.created_at == datetime(2026, 11, 7, 19, 18, 44, tzinfo=timezone(timedelta(hours=1)))
    assert (
        row.text
        == "First Second runbook Links: https://example.test/runbook Attachment: replay.txt"
    )
