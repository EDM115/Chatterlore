from __future__ import annotations

import re
from collections.abc import Iterable

KEY_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)([^\s,;]+)")
QUOTED_KEY_VALUE_RE = re.compile(
    r"""
    (?P<prefix>(?P<key_quote>['"])(?:api[_-]?key|token|password|secret)(?P=key_quote)\s*:\s*)
    (?P<value_quote>['"])(?:\\.|(?!(?P=value_quote)).)*(?P=value_quote)
    """,
    re.IGNORECASE | re.VERBOSE,
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}")
COMMON_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,})\b"
)


def redact_text(text: str, custom_patterns: Iterable[str] = ()) -> str:
    redacted = QUOTED_KEY_VALUE_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('value_quote')}[REDACTED]{match.group('value_quote')}"
        ),
        text,
    )
    redacted = KEY_VALUE_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
    )
    redacted = BEARER_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    redacted = COMMON_TOKEN_RE.sub("[REDACTED_TOKEN]", redacted)
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", redacted)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    for pattern in custom_patterns:
        redacted = re.sub(pattern, "[REDACTED_CUSTOM]", redacted)
    return redacted
