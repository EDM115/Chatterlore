from __future__ import annotations

import asyncio
import hashlib
import importlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chatterlore.local_models import LocalModelOptions

LOCAL_TOKENIZER_FILES = frozenset(
    {
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "added_tokens.json",
        "tokenizer.model",
        "spiece.model",
        "vocab.json",
        "vocab.txt",
        "merges.txt",
        "chat_template.jinja",
    }
)
LOCAL_CONFIG_FILES = frozenset(
    {
        "config.json",
        "generation_config.json",
        "modules.json",
        "config_sentence_transformers.json",
        "sentence_bert_config.json",
    }
)


def local_directory_revision(directory: str) -> str:
    root = Path(directory).resolve()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.name in LOCAL_TOKENIZER_FILES | LOCAL_CONFIG_FILES
            or path.suffix == ".safetensors"
            or path.name.endswith(".safetensors.index.json")
        )
    )
    if not any(path.suffix == ".safetensors" for path in files):
        raise ValueError("local_preparation_unsupported")
    digest = hashlib.sha256()
    for path in files:
        if not path.resolve().is_relative_to(root):
            raise ValueError("local_preparation_unsupported")
        before = path.stat()
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("local_artifact_revision_changed")
    return f"sha256:{digest.hexdigest()}"


def validate_local_artifact_revision(model_or_path: str, revision: str | None) -> None:
    if (
        revision
        and revision.startswith("sha256:")
        and local_directory_revision(model_or_path) != revision
    ):
        raise ValueError("local_artifact_revision_changed")


class LocalInferenceWorker:
    """One model operation at a time, including after caller cancellation."""

    def __init__(self, options: LocalModelOptions) -> None:
        self.options = options
        self._lock = asyncio.Lock()
        self._pending = 0
        self._closed = False
        self._stop: threading.Event | None = None
        self._task: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._pending > 0 or self._lock.locked()

    async def run[T](self, operation: Callable[[threading.Event], T]) -> T:
        if self._closed:
            raise RuntimeError("Local inference provider is closed")
        if self._pending >= self.options.max_queue_size + 1:
            raise RuntimeError(
                "Local inference queue is full, retry after the active request finishes"
            )
        self._pending += 1
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self.options.queue_timeout_seconds)
        except TimeoutError:
            self._pending -= 1
            raise TimeoutError("Local inference queue wait timed out") from None
        except BaseException:
            self._pending -= 1
            raise
        if self._closed:
            self._pending -= 1
            self._lock.release()
            raise RuntimeError("Local inference provider is closed")

        stop = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(operation, stop))
        self._task = task
        self._stop = stop

        def finished(completed: asyncio.Task) -> None:
            # A cancelled caller must never make the still-running thread's model reusable
            if not completed.cancelled():
                completed.exception()
            self._task = None
            self._stop = None
            self._pending -= 1
            self._lock.release()

        task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), timeout=self.options.inference_timeout_seconds
            )
        except TimeoutError:
            stop.set()
            raise TimeoutError(
                "Local inference timed out, the worker is stopping before its slot is released"
            ) from None
        except asyncio.CancelledError:
            stop.set()
            raise

    async def close(self, release: Callable[[], None]) -> None:
        self._closed = True
        if self._stop is not None:
            self._stop.set()
        async with self._lock:
            await asyncio.to_thread(release)


def import_local_dependency(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        raise RuntimeError(
            "Local dependencies unavailable, run `uv sync --extra local` or change provider"
        ) from None


def validate_prepared_paths(
    *, allow_download: bool, model_path: str | None, tokenizer_path: str | None
) -> tuple[str | None, str | None]:
    if allow_download and model_path is None:
        raise ValueError(
            "Use the bounded preparation service to download and validate local model files"
        )
    resolved: list[str | None] = []
    for path in (model_path, tokenizer_path):
        if path is None:
            resolved.append(None)
            continue
        try:
            directory = Path(path).resolve(strict=True)
            if not directory.is_dir():
                raise ValueError("Prepared local model paths must be existing directories")
        except OSError:
            raise ValueError("Prepared local model paths must be existing directories") from None
        resolved.append(str(directory))
    return resolved[0], resolved[1]


def local_loader_revision(model_or_path: str, revision: str | None) -> str | None:
    # A directory's semantic digest is provenance, not a Hugging Face Hub revision
    try:
        return None if Path(model_or_path).is_dir() else revision
    except OSError:
        return revision


def resolve_device_dtype(torch: Any, options: LocalModelOptions) -> tuple[str, Any]:
    requested = options.device
    device = "cuda:0" if requested == "auto" and torch.cuda.is_available() else requested
    if device == "auto":
        device = "cpu"
    if device == "cuda":
        device = "cuda:0"
    if device.startswith("cuda:"):
        if not torch.cuda.is_available() or int(device[5:]) >= torch.cuda.device_count():
            raise ValueError("The configured local CUDA device is unavailable")
        if options.dtype == "bfloat16":
            with torch.cuda.device(device):
                if not torch.cuda.is_bf16_supported():
                    raise ValueError("The configured local CUDA device does not support bfloat16")
    elif options.dtype == "float16":
        raise ValueError(
            "Local float16 inference requires a supported CUDA device, use float32 on CPU"
        )
    dtype = options.dtype
    if dtype == "auto" and device == "cpu":
        dtype = "float32"
    return device, "auto" if dtype == "auto" else getattr(torch, dtype)
