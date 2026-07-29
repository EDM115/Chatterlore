from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from typing import Any

from pzp_ai_bot.ai.protocols import ChatMessage, ChatResult


class LocalSentenceTransformerEmbeddingProvider:
    """Local embedding provider using sentence-transformers when installed."""

    name = "local"

    def __init__(
        self, model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    ) -> None:
        self.model = "local-chat-unavailable"
        self.embedding_model = model
        self._model = None
        self.dimensions = 384

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        content = (
            "Local chat generation is not configured in v1. Use an OpenAI-compatible chat provider."
        )
        return ChatResult(content=content, model=self.model)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = await self._load()
        vectors = await asyncio.to_thread(model.encode, list(texts), normalize_embeddings=True)
        out = [list(map(float, vector)) for vector in vectors]
        if out:
            self.dimensions = len(out[0])
        return out

    async def _load(self):
        if self._model is None:
            try:
                module: Any = importlib.import_module("sentence_transformers")
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "sentence-transformers is required for the local embedding provider. "
                    "Install it with `uv sync --extra local` or choose "
                    "AI_PROVIDER=fake/openai_compatible."
                ) from exc
            self._model = await asyncio.to_thread(module.SentenceTransformer, self.embedding_model)
        return self._model
