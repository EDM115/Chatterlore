from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from numbers import Real
from typing import Any, Literal

from chatterlore.ai.local_chat import LocalTransformersChatProvider
from chatterlore.ai.local_runtime import (
    LocalInferenceWorker,
    import_local_dependency,
    local_loader_revision,
    resolve_device_dtype,
    validate_local_artifact_revision,
    validate_prepared_paths,
)
from chatterlore.ai.protocols import ChatMessage, ChatResult
from chatterlore.local_models import LocalModelOptions

__all__ = ["LocalSentenceTransformerEmbeddingProvider", "LocalTransformersChatProvider"]


class LocalSentenceTransformerEmbeddingProvider:
    """Lazy local embeddings with explicit preparation and serialized model access."""

    name = "local"

    def __init__(
        self,
        model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        *,
        options: LocalModelOptions | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("A local embedding model must be configured")
        self.model = model
        self.embedding_model = model
        self.options = options or LocalModelOptions()
        self._model: Any = None
        self._prepared_model_path: str | None = None
        self._prepared_tokenizer_path: str | None = None
        self._worker = LocalInferenceWorker(self.options)
        self.dimensions = 384

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def busy(self) -> bool:
        return self._worker.busy

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 700,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        raise ValueError("Embedding-only profile, configure a local chat model for text generation")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, "generic")

    async def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, "query")

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, "document")

    async def _embed(
        self, texts: Sequence[str], purpose: Literal["generic", "query", "document"]
    ) -> list[list[float]]:
        if len(texts) > self.options.max_batch_inputs:
            raise ValueError("Local embedding batch exceeds the configured input count limit")
        if any(not isinstance(text, str) for text in texts):
            raise ValueError("Local embedding inputs must be text")
        if sum(map(len, texts)) > self.options.max_input_characters:
            raise ValueError("Local embedding input exceeds the configured character limit")
        if not texts:
            return []
        return await self._worker.run(lambda stop: self._encode_sync(list(texts), purpose, stop))

    def _encode_sync(
        self, texts: list[str], purpose: str, stop: threading.Event
    ) -> list[list[float]]:
        model = self._load_sync(local_files_only=True)
        if stop.is_set():
            raise RuntimeError("Local embedding inference was cancelled before encoding")
        encode = model.encode
        if self.options.prompt_mode == "query_document" and purpose != "generic":
            encode = model.encode_query if purpose == "query" else model.encode_document
        try:
            vectors = encode(
                texts,
                batch_size=self.options.batch_size,
                normalize_embeddings=self.options.normalize_embeddings,
                truncate_dim=self.options.truncate_dim,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            if any(
                not isinstance(value, Real) or isinstance(value, bool)
                for vector in vectors
                for value in vector
            ):
                raise ValueError("Non-numeric embedding value")
            out = [list(map(float, vector)) for vector in vectors]
        except Exception:
            raise RuntimeError(
                "Local embedding inference failed, verify the configured model and device"
            ) from None
        if len(out) != len(texts) or any(
            len(vector) != self.dimensions or not all(math.isfinite(value) for value in vector)
            for vector in out
        ):
            raise RuntimeError(
                "Local embedding output has invalid count, dimensions or non-finite values"
            )
        return out

    def _load_sync(
        self,
        *,
        local_files_only: bool,
        model_path: str | None = None,
        tokenizer_path: str | None = None,
    ) -> Any:
        if self._model is not None:
            if (model_path is not None and model_path != self._prepared_model_path) or (
                tokenizer_path is not None and tokenizer_path != self._prepared_tokenizer_path
            ):
                raise ValueError(
                    "Retire the loaded local model before preparing a different snapshot"
                )
            return self._model
        model_reference = model_path or self.embedding_model
        validate_local_artifact_revision(model_reference, self.options.revision)
        if tokenizer_path is not None and tokenizer_path != model_reference:
            validate_local_artifact_revision(
                tokenizer_path, self.options.tokenizer_revision or self.options.revision
            )
        module = import_local_dependency("sentence_transformers")
        torch = import_local_dependency("torch")
        device, dtype = resolve_device_dtype(torch, self.options)
        try:
            model = module.SentenceTransformer(
                model_reference,
                revision=local_loader_revision(model_reference, self.options.revision),
                device=device,
                cache_folder=self.options.cache_dir,
                local_files_only=True,
                trust_remote_code=False,
                truncate_dim=self.options.truncate_dim,
                model_kwargs={
                    "use_safetensors": True,
                    "trust_remote_code": False,
                    "local_files_only": True,
                    "dtype": dtype,
                },
                processor_kwargs={
                    "revision": local_loader_revision(
                        tokenizer_path or model_reference,
                        self.options.tokenizer_revision or self.options.revision,
                    ),
                    "trust_remote_code": False,
                    "local_files_only": True,
                },
            )
            if tokenizer_path is not None and tokenizer_path != model_path:
                first = model[0]
                if (
                    getattr(first, "processor", None) is not getattr(first, "tokenizer", None)
                    or getattr(first, "processor", None) is None
                ):
                    raise ValueError(
                        "This embedding model does not support a separate text tokenizer snapshot"
                    )
                transformers = import_local_dependency("transformers")
                first.processor = transformers.AutoTokenizer.from_pretrained(
                    tokenizer_path, trust_remote_code=False, local_files_only=True
                )
            dimensions = model.get_embedding_dimension()
            if not isinstance(dimensions, int) or dimensions < 1:
                raise ValueError("Invalid model dimensions")
            if self.options.truncate_dim is not None:
                if self.options.truncate_dim > dimensions:
                    raise ValueError("Invalid truncation dimensions")
                dimensions = self.options.truncate_dim
            sequence_limit = model.max_seq_length
            model.max_seq_length = (
                min(sequence_limit, self.options.max_input_tokens)
                if isinstance(sequence_limit, int)
                else self.options.max_input_tokens
            )
        except Exception:
            raise RuntimeError(
                "Cannot load local embeddings, check dependencies and explicitly prepare cache"
            ) from None
        self.dimensions = dimensions
        self._model = model
        self._prepared_model_path = model_path
        self._prepared_tokenizer_path = tokenizer_path
        return self._model

    async def prepare(
        self,
        *,
        allow_download: bool = False,
        model_path: str | None = None,
        tokenizer_path: str | None = None,
    ) -> None:
        def prepare_sync(stop: threading.Event) -> None:
            prepared_model, prepared_tokenizer = validate_prepared_paths(
                allow_download=allow_download, model_path=model_path, tokenizer_path=tokenizer_path
            )
            self._load_sync(
                local_files_only=True, model_path=prepared_model, tokenizer_path=prepared_tokenizer
            )

        await self._worker.run(prepare_sync)

    async def close(self) -> None:
        def release() -> None:
            self._model = None

        await self._worker.close(release)

    async def aclose(self) -> None:
        await self.close()
