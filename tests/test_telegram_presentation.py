from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyParameters

from pzp_ai_bot.telegram import safe_api
from pzp_ai_bot.telegram.safe_api import safe_answer


class RichContentProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.code = ""
        self.rows: list[str] = []
        self.in_code = False
        self.in_row = False
        self.in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "code":
            self.in_code = True
        if tag == "tr":
            self.in_row = True
            self.rows.append("")
        if tag in {"td", "th"}:
            self.in_cell = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "code":
            self.in_code = False
        if tag == "tr":
            self.in_row = False
        if tag in {"td", "th"}:
            self.in_cell = False

    def handle_data(self, data: str) -> None:
        if self.in_code:
            self.code += data
        if self.in_row and self.in_cell:
            self.rows[-1] += data


class PresentationSession(BaseSession):
    def __init__(self, failure: str | None = None) -> None:
        super().__init__()
        self.calls: list[Any] = []
        self.failure = failure

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.calls.append(method)
        if method.__api_method__ == "sendRichMessage":
            if self.failure == "format":
                raise TelegramBadRequest(method, "can't parse rich message: PRIVATE_CONTENT")
            if self.failure == "network":
                raise TelegramNetworkError(method, "PRIVATE_CONTENT transport detail")
            if self.failure == "forbidden":
                raise TelegramForbiddenError(method, "PRIVATE_CONTENT forbidden detail")
            if self.failure == "rate":
                raise TelegramRetryAfter(method, "PRIVATE_CONTENT rate detail", 12)
        return Message(
            message_id=900 + len(self.calls),
            date=datetime(2026, 11, 7, tzinfo=UTC),
            chat=Chat(id=int(getattr(method, "chat_id", 0)), type="supergroup"),
            text=getattr(method, "text", None),
        ).as_(bot)

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:
            yield b""


def incoming(bot: Bot) -> Message:
    return Message(
        message_id=47,
        date=datetime(2026, 11, 7, tzinfo=UTC),
        chat=Chat(id=-100123456, type="supergroup", is_forum=True),
        message_thread_id=3,
        is_topic_message=True,
        business_connection_id="business-sentinel",
        text="/ask formatting",
    ).as_(bot)


@pytest.mark.asyncio
async def test_markdown_reply_uses_native_rich_formatting_and_keeps_destination_metadata() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    text = "# Result\n\n**Ready** and `code_x`\n\n| Key | Value |\n| --- | --- |\n| A | 7 |"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Refresh", callback_data="refresh")]]
    )

    assert await safe_answer(
        incoming(bot),
        text,
        markdown=True,
        reply_markup=keyboard,
        reply_parameters=ReplyParameters(message_id=47),
    )

    [method] = session.calls
    assert method.__api_method__ == "sendRichMessage"
    payload = method.model_dump(exclude_none=True)
    html = payload["rich_message"]["html"]
    assert "<h1>Result</h1>" in html
    assert "<strong>Ready</strong>" in html and "<code>code_x</code>" in html
    assert "<table>" in html and "<td>7</td>" in html
    assert payload["chat_id"] == -100123456
    assert payload["message_thread_id"] == 3
    assert payload["business_connection_id"] == "business-sentinel"
    assert payload["reply_parameters"]["message_id"] == 47
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "refresh"


@pytest.mark.asyncio
async def test_application_reply_formats_its_heading_but_keeps_dynamic_values_literal() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)

    assert await safe_answer(
        incoming(bot), "Scope settings:\nprovider_profile: a_b\n<fake> **raw**"
    )

    payload = session.calls[0].model_dump(exclude_none=True)["rich_message"]
    assert payload["blocks"] == [
        {"type": "heading", "text": "Scope settings:", "size": 3},
        {"type": "paragraph", "text": "provider_profile: a_b\n<fake> **raw**"},
    ]
    assert "markdown" not in payload


@pytest.mark.asyncio
async def test_generated_markup_cannot_embed_remote_media_or_telegram_controls() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    raw = '# Title\n![track](https://example.invalid/image.png)\n<tg-button type="url" url="https://example.invalid">Open</tg-button>\n<img src="https://example.invalid/other.png">'  # noqa: E501

    assert await safe_answer(incoming(bot), raw, markdown=True)

    html = session.calls[0].rich_message.html
    assert "<tg-button" not in html and "<img" not in html
    assert "<h1>Title</h1>" in html
    assert "https://example.invalid/image.png" in html
    assert not session.calls[0].rich_message.media


@pytest.mark.asyncio
async def test_code_markup_stays_literal_while_surrounding_markdown_is_formatted() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)

    assert await safe_answer(
        incoming(bot), '**Example**\n```html\n<img src="not-to-fetch">\n```', markdown=True
    )

    html = session.calls[0].rich_message.html
    assert "<strong>Example</strong>" in html
    assert "&lt;img src=&quot;not-to-fetch&quot;&gt;" in html
    assert "<img src=" not in html


@pytest.mark.asyncio
async def test_explicit_format_rejection_falls_back_to_literal_unicode_safe_messages() -> None:
    session = PresentationSession("format")
    bot = Bot("123456:TEST_TOKEN", session=session)
    text = "**" + "😀_x&<>" * 1100 + "**"

    assert await safe_answer(incoming(bot), text, markdown=True)

    assert session.calls[0].__api_method__ == "sendRichMessage"
    sent = session.calls[1:]
    assert len(sent) > 1 and all(method.__api_method__ == "sendMessage" for method in sent)
    assert "".join(method.text for method in sent) == text
    assert all(len(method.text.encode("utf-16-le")) // 2 <= 4096 for method in sent)
    assert all(
        method.parse_mode is None and method.link_preview_options.is_disabled for method in sent
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network", "rate", "forbidden"])
async def test_ambiguous_or_forbidden_rich_failure_does_not_send_another_copy(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = PresentationSession(failure)
    bot = Bot("123456:TEST_TOKEN", session=session)
    warnings: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        safe_api.logger, "warning", lambda event, **values: warnings.append((event, values))
    )

    assert not await safe_answer(incoming(bot), "PRIVATE_CONTENT", markdown=True)

    assert [method.__api_method__ for method in session.calls] == ["sendRichMessage"]
    assert warnings and "PRIVATE_CONTENT" not in str(warnings)


@pytest.mark.asyncio
async def test_long_literal_reply_is_complete_without_exceeding_rich_byte_limits() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    text = "😀é value_a\n" * 4000

    assert await safe_answer(incoming(bot), text)

    payloads = [method.rich_message for method in session.calls]
    assert len(payloads) > 1
    assert "".join(str(block.text) for payload in payloads for block in payload.blocks) == text
    assert all(
        len(str(block.text).encode("utf-8")) <= 32768
        for payload in payloads
        for block in payload.blocks
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_rich", [False, True])
async def test_long_literal_delivery_prefers_complete_sentences_without_losing_whitespace(
    reject_rich: bool,
) -> None:
    session = PresentationSession("format" if reject_rich else None)
    bot = Bot("123456:TEST_TOKEN", session=session)
    sentence = "This is a complete sentence with Unicode 😀 and an ordinary ending. "
    text = sentence * 900

    assert await safe_answer(incoming(bot), text)

    parts = [
        method.text
        if method.__api_method__ == "sendMessage"
        else str(method.rich_message.blocks[0].text)
        for method in session.calls
        if method.__api_method__ == "sendMessage" or not reject_rich
    ]
    assert len(parts) > 1 and "".join(parts) == text
    assert all(part.rstrip().endswith(".") for part in parts)


@pytest.mark.asyncio
@pytest.mark.parametrize("group_finalization", [False, True])
async def test_long_fenced_code_stays_code_in_every_chunk_including_group_finalization(
    group_finalization: bool,
) -> None:
    from pzp_ai_bot.telegram.streaming import AnswerDelivery

    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    line = '**literal_&<tag>** `name` and "quotes"\n'
    code = line * 1300
    text = "**Example**\n\n```text\n" + code + "```\n\nDone. [source:17]"
    if group_finalization:
        delivery = AnswerDelivery(incoming(bot))
        await delivery.update("Preview")
        assert await delivery.finish(text)
        methods = session.calls[1:]
    else:
        assert await safe_answer(incoming(bot), text, markdown=True)
        methods = session.calls

    probes = []
    for method in methods:
        probe = RichContentProbe()
        probe.feed(method.rich_message.html or "")
        probes.append(probe)
        assert len((method.rich_message.html or "").encode("utf-8")) <= 32768
    assert len(methods) > 1
    assert "".join(probe.code for probe in probes) == code
    assert all(probe.code.endswith("\n") for probe in probes if probe.code)
    assert "[source:17]" in methods[-1].rich_message.html


@pytest.mark.asyncio
async def test_large_table_splits_at_complete_rows_and_repeats_the_header() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    rows = [f"| R{index:04d} | value-{index:04d} |\n" for index in range(700)]
    text = "| Key | Value |\n| --- | --- |\n" + "".join(rows)

    assert await safe_answer(incoming(bot), text, markdown=True)

    assert len(session.calls) > 1
    actual_rows = []
    for method in session.calls:
        html = method.rich_message.html
        probe = RichContentProbe()
        probe.feed(html)
        assert probe.rows[0] == "KeyValue"
        assert len(probe.rows) < 400
        assert html.count("<table>") == html.count("</table>") == 1
        actual_rows.extend(probe.rows[1:])
    assert actual_rows == [f"R{index:04d}value-{index:04d}" for index in range(700)]


@pytest.mark.asyncio
async def test_markdown_chunking_keeps_complete_paragraphs_and_reference_links() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    paragraph = "**Complete paragraph.** " * 450 + "[Guide][guide]\n\n"
    text = paragraph * 6 + "[guide]: https://example.test/guide\n"

    assert await safe_answer(incoming(bot), text, markdown=True)

    assert len(session.calls) > 1
    for method in session.calls:
        html = method.rich_message.html
        assert html.count("<strong>") % 450 == 0
        assert html.count('<a href="https://example.test/guide">Guide</a>') >= 1


@pytest.mark.asyncio
async def test_large_ordered_list_preserves_item_boundaries_and_continued_numbering() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    text = "".join(f"{index}. **Item {index:04d}**\n" for index in range(1, 801))

    assert await safe_answer(incoming(bot), text, markdown=True)

    assert len(session.calls) > 1
    delivered = "".join(method.rich_message.html for method in session.calls)
    assert delivered.count("<li>") == delivered.count("</li>") == 800
    assert all(f"<strong>Item {index:04d}</strong>" in delivered for index in range(1, 801))
    next_item = 1
    for method in session.calls:
        html = method.rich_message.html
        assert html.count("<ol") == html.count("</ol>") == 1
        if next_item > 1:
            assert f'<ol start="{next_item}">' in html
        next_item += html.count("<li>")


@pytest.mark.asyncio
async def test_rejected_long_code_and_tables_preserve_the_original_literal_source() -> None:
    session = PresentationSession("format")
    bot = Bot("123456:TEST_TOKEN", session=session)
    code = "**code_literal** <value>\n" * 1400
    table = "| Key | Value |\n| --- | --- |\n" + "| A | B |\n" * 800
    text = "Before.\n\n```text\n" + code + "```\n\n" + table

    assert await safe_answer(incoming(bot), text, markdown=True)

    literal = [method.text for method in session.calls if method.__api_method__ == "sendMessage"]
    assert "".join(literal) == text


@pytest.mark.asyncio
async def test_indivisible_oversized_table_row_stays_literal_without_losing_content() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    text = "| Key | Value |\n| --- | --- |\n| A | " + "x" * 40000 + " |\n"

    assert await safe_answer(incoming(bot), text, markdown=True)

    assert len(session.calls) > 1
    html = "".join(method.rich_message.html for method in session.calls)
    assert html.count("x") == 40000
    assert all(len(method.rich_message.html.encode("utf-8")) <= 32768 for method in session.calls)


@pytest.mark.asyncio
async def test_code_chunk_budget_includes_escaped_language_metadata_and_wrapper_overhead() -> None:
    session = PresentationSession()
    bot = Bot("123456:TEST_TOKEN", session=session)
    code = "<&>\n" * 5000
    text = "```" + "language" * 3200 + "\n" + code + "```\n"

    assert await safe_answer(incoming(bot), text, markdown=True)

    actual = ""
    for method in session.calls:
        html = method.rich_message.html
        assert len(html.encode("utf-8")) <= 24576
        probe = RichContentProbe()
        probe.feed(html)
        actual += probe.code
    assert actual == code
