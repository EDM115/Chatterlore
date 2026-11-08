from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from html import escape
from typing import Any

from aiogram.types import InputRichBlockParagraph, InputRichBlockSectionHeading, InputRichMessage
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

RICH_TEXT_BYTES = 24 * 1024
PLAIN_TEXT_UNITS = 4096
RICH_BLOCKS = 400

# Keep generated HTML and media inert
_MARKDOWN = MarkdownIt("js-default", {"html": False, "maxNesting": 12}).disable("image")


def split_text(
    text: str, *, limit: int = RICH_TEXT_BYTES, utf16: bool = False, escaped: bool = False
) -> Iterator[str]:
    """Keep complete paragraphs, lines, sentences or words when the hard limit permits."""
    start = 0
    used = 0
    index = 0
    while index < len(text):
        char = text[index]
        value = escape(char) if escaped else char
        size = len(value.encode("utf-16-le")) // 2 if utf16 else len(value.encode("utf-8"))
        if used + size > limit and index > start:
            end = _logical_end(text, start, index)
            yield text[start:end]
            start = index = end
            used = 0
            continue
        used += size
        index += 1
    if start < len(text):
        yield text[start:]


def _logical_end(text: str, start: int, end: int) -> int:
    window = text[start:end]
    for separator in ("\n\n", "\n"):
        at = window.rfind(separator)
        if at >= 0:
            return start + at + len(separator)
    for pattern in (r"[.!?][\"')\]]*\s+", r"\s+"):
        last = next(reversed(list(re.finditer(pattern, window))), None)
        if last is not None:
            return start + last.end()
    return end


@dataclass(frozen=True)
class AnswerChunk:
    text: str
    payload: InputRichMessage


def answer_chunks(text: str, *, markdown: bool = False) -> Iterator[AnswerChunk]:
    if not markdown:
        for part in split_text(text):
            yield AnswerChunk(part, rich_payload(part))
        return
    env: dict[str, Any] = {}
    root = SyntaxTreeNode(_MARKDOWN.parse(text, env))
    if not root.children:
        for part in split_text(text):
            yield AnswerChunk(part, rich_payload(part))
        return
    pending_text = ""
    pending_html = ""
    pending_blocks = 0
    for node, source, line in _node_sources(root.children, text, 0):
        for raw, html, blocks in _markdown_fragments(node, source, line, env):
            if pending_html and (
                len((pending_html + html).encode("utf-8")) > RICH_TEXT_BYTES
                or pending_blocks + blocks > RICH_BLOCKS
            ):
                yield AnswerChunk(
                    pending_text, InputRichMessage(html=pending_html, skip_entity_detection=True)
                )
                pending_text = pending_html = ""
                pending_blocks = 0
            pending_text += raw
            pending_html += html
            pending_blocks += blocks
    if pending_html:
        yield AnswerChunk(
            pending_text, InputRichMessage(html=pending_html, skip_entity_detection=True)
        )


def _node_sources(
    nodes: list[SyntaxTreeNode], source: str, first_line: int
) -> Iterator[tuple[SyntaxTreeNode, str, int]]:
    lines = source.splitlines(keepends=True)
    cursor = 0
    for index, node in enumerate(nodes):
        next_map = nodes[index + 1].map if index + 1 < len(nodes) else None
        end = next_map[0] - first_line if next_map is not None else len(lines)
        yield node, "".join(lines[cursor:end]), first_line + cursor
        cursor = end


def _markdown_fragments(
    node: SyntaxTreeNode,
    source: str,
    first_line: int,
    env: dict[str, Any],
    prefix: str = "",
    suffix: str = "",
    ancestor_blocks: int = 0,
) -> Iterator[tuple[str, str, int]]:
    tokens = node.to_tokens()
    html = prefix + _MARKDOWN.renderer.render(tokens, _MARKDOWN.options, env) + suffix
    blocks = ancestor_blocks + sum(
        token.block and token.nesting >= 0 and token.type != "inline" for token in tokens
    )
    if len(html.encode("utf-8")) <= RICH_TEXT_BYTES and blocks <= RICH_BLOCKS:
        yield source, html, blocks
        return
    if node.type in {"bullet_list", "ordered_list", "blockquote", "list_item"}:
        assert node.nester_tokens is not None
        opening, closing = node.nester_tokens
        end = _MARKDOWN.renderer.render([closing], _MARKDOWN.options, env) + suffix

        def start(index: int) -> str:
            start_token = opening
            if node.type == "ordered_list":
                start_token = replace(
                    opening,
                    attrs={**opening.attrs, "start": int(opening.attrs.get("start", 1)) + index},
                )
            return prefix + _MARKDOWN.renderer.render([start_token], _MARKDOWN.options, env)

        yield from _wrapped_fragments(
            node.children, source, first_line, env, start, end, ancestor_blocks + 1
        )
        return
    if node.type == "table" and len(node.children) == 2:
        head, body = node.children
        assert node.nester_tokens is not None and body.nester_tokens is not None
        opening, closing = node.nester_tokens
        body_open, body_close = body.nester_tokens
        header_tokens = [opening, *head.to_tokens(), body_open]
        header = prefix + _MARKDOWN.renderer.render(header_tokens, _MARKDOWN.options, env)
        footer = _MARKDOWN.renderer.render([body_close, closing], _MARKDOWN.options, env) + suffix
        header_blocks = sum(token.block and token.nesting >= 0 for token in header_tokens)
        yield from _wrapped_fragments(
            body.children,
            source,
            first_line,
            env,
            lambda _: header,
            footer,
            ancestor_blocks + header_blocks,
        )
        return
    if node.type in {"fence", "code_block"}:
        assert node.token is not None
        token = node.token
        render_token = token if len(token.info.encode("utf-8")) <= 128 else replace(token, info="")
        empty = (
            prefix
            + _MARKDOWN.renderer.render([replace(render_token, content="")], _MARKDOWN.options, env)
            + suffix
        )
        budget = RICH_TEXT_BYTES - len(empty.encode("utf-8"))
        parts = list(split_text(token.content, limit=max(1, budget), escaped=True)) or [""]
        lines = source.splitlines(keepends=True)
        source_start = (node.map[0] if node.map is not None else first_line) - first_line
        source_end = (node.map[1] if node.map is not None else first_line + len(lines)) - first_line
        content_start = source_start + (node.type == "fence")
        raw_prefix = "".join(lines[:content_start])
        raw_suffix = "".join(lines[source_end:])
        if node.type == "fence" and source_end > content_start:
            fence = re.escape(token.markup[0]) + "{" + str(len(token.markup)) + ",}"
            if re.fullmatch(r"\s*" + fence + r"[ \t]*\r?\n?", lines[source_end - 1]):
                raw_suffix = lines[source_end - 1] + raw_suffix
        for index, part in enumerate(parts):
            raw = (raw_prefix if index == 0 else "") + part
            if index == len(parts) - 1:
                raw += raw_suffix
            rendered = _MARKDOWN.renderer.render(
                [replace(render_token, content=part)], _MARKDOWN.options, env
            )
            yield raw, prefix + rendered + suffix, ancestor_blocks + 1
        return
    # An indivisible oversized row/inline block stays literal instead of reparsing broken Markdown
    for part in split_text(source, limit=RICH_TEXT_BYTES - 64, escaped=True):
        yield part, "<pre>" + escape(part) + "</pre>\n", 1


def _wrapped_fragments(
    nodes: list[SyntaxTreeNode],
    source: str,
    first_line: int,
    env: dict[str, Any],
    opening: Callable[[int], str],
    closing: str,
    ancestor_blocks: int,
) -> Iterator[tuple[str, str, int]]:
    raw_pending = ""
    html_pending = ""
    blocks_pending = 0
    start = opening(0)
    for index, (node, raw, line) in enumerate(_node_sources(nodes, source, first_line)):
        tokens = node.to_tokens()
        html = _MARKDOWN.renderer.render(tokens, _MARKDOWN.options, env)
        blocks = sum(
            token.block and token.nesting >= 0 and token.type != "inline" for token in tokens
        )
        if html_pending and (
            len((start + html_pending + html + closing).encode("utf-8")) > RICH_TEXT_BYTES
            or ancestor_blocks + blocks_pending + blocks > RICH_BLOCKS
        ):
            yield raw_pending, start + html_pending + closing, ancestor_blocks + blocks_pending
            raw_pending = html_pending = ""
            blocks_pending = 0
            start = opening(index)
        if (
            len((start + html + closing).encode("utf-8")) > RICH_TEXT_BYTES
            or ancestor_blocks + blocks > RICH_BLOCKS
        ):
            yield from _markdown_fragments(node, raw, line, env, start, closing, ancestor_blocks)
            start = opening(index + 1)
            continue
        raw_pending += raw
        html_pending += html
        blocks_pending += blocks
    if html_pending:
        yield raw_pending, start + html_pending + closing, ancestor_blocks + blocks_pending


def markdown_has_text(text: str) -> bool:
    tokens = list(_MARKDOWN.parse(text))
    while tokens:
        token = tokens.pop()
        if token.type in {"text", "code_inline", "code_block", "fence"} and token.content.strip():
            return True
        if token.children:
            tokens.extend(token.children)
    return False


def rich_payload(text: str, *, markdown: bool = False) -> InputRichMessage:
    if markdown:
        return InputRichMessage(html=_MARKDOWN.render(text), skip_entity_detection=True)
    first, newline, rest = text.partition("\n")
    if newline and first.endswith(":") and len(first) <= 80:
        return InputRichMessage(
            blocks=[
                InputRichBlockSectionHeading(text=first, size=3),
                InputRichBlockParagraph(text=rest),
            ],
            skip_entity_detection=True,
        )
    return InputRichMessage(blocks=[InputRichBlockParagraph(text=text)], skip_entity_detection=True)
