from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult

WORD_RE = re.compile(r"[\w']+", re.UNICODE)


class FakeProvider:
    """Deterministic chat and embedding provider used by tests and offline demos."""

    name = "fake"

    def __init__(
        self,
        chat_model: str = "fake-chat",
        embedding_model: str = "fake-embedding",
        dimensions: int = 64,
    ) -> None:
        self.model = chat_model
        self.embedding_model = embedding_model
        self.dimensions = dimensions

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        joined = "\n".join(message.content for message in messages)
        if "Summarize durable group-chat memory" in joined:
            content = self._summary(joined)
        elif "Canonicalize durable group-chat memory" in joined:
            content = self._canonical(joined)
        elif "Answer from scoped memory" in joined:
            content = self._answer(joined)
        else:
            content = "Fake response based on provided context."
        return ChatResult(
            content=content[: max_tokens * 4],
            model=self.model,
            prompt_tokens=len(joined.split()),
            completion_tokens=len(content.split()),
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        words = WORD_RE.findall(text.lower())
        if not words:
            words = [text.lower()]
        for word in words:
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=16).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def _summary(self, prompt: str) -> str:
        lines = [
            line.strip("- ").strip()
            for line in prompt.splitlines()
            if line.strip().startswith("- ")
        ]
        bullets = []
        for line in lines[:8]:
            if not line:
                continue
            source = ""
            match = re.search(r"\[source:([^\]]+)\]", line)
            if match:
                source = f" [source:{match.group(1)}]"
            cleaned = re.sub(r"\s*\[source:[^\]]+\]", "", line).strip()
            bullets.append(f"- {cleaned}{source}")
        return "\n".join(bullets) if bullets else "- No durable facts found."

    def _canonical(self, prompt: str) -> str:
        grouped: dict[str, list[str]] = {
            "Facts": [],
            "Decisions": [],
            "Preferences": [],
            "Open Questions": [],
            "Todos": [],
        }
        for raw_line in prompt.splitlines():
            line = raw_line.strip()
            if not line.startswith("- [item:"):
                continue
            lower = line.lower()
            if "todo" in lower or " needs to " in lower or " should " in lower:
                category = "Todos"
            elif "decision" in lower or " decided" in lower:
                category = "Decisions"
            elif "preference" in lower or " prefers " in lower:
                category = "Preferences"
            elif line.endswith("?") or "open question" in lower:
                category = "Open Questions"
            else:
                category = "Facts"
            grouped[category].append(line)
        lines: list[str] = []
        for category, entries in grouped.items():
            if not entries:
                continue
            lines.append(category)
            lines.extend(entries[:12])
        return "\n".join(lines) if lines else "Facts\n- No durable facts found."

    def _answer(self, prompt: str) -> str:
        context_lines = [
            line.strip()
            for line in prompt.splitlines()
            if line.strip().startswith("[source:") or line.strip().startswith("- ")
        ]
        if not context_lines:
            return "I do not have scoped memory for that yet."
        return "From scoped memory:\n" + "\n".join(context_lines[:6])
