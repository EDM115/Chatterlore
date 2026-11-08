from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LocalModelOptions(BaseModel):
    """Immutable, bounded controls shared by optional embedded inference backends."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    revision: str | None = Field(default=None, max_length=256)
    tokenizer_revision: str | None = Field(default=None, max_length=256)
    device: str = "auto"
    dtype: Literal["auto", "float32", "float16", "bfloat16"] = "auto"
    cache_dir: str | None = Field(default=None, max_length=4096)
    local_files_only: bool = True
    batch_size: int = Field(default=32, ge=1, le=1024)
    normalize_embeddings: bool = True
    truncate_dim: int | None = Field(default=None, ge=1, le=65536)
    prompt_mode: Literal["generic", "query_document"] = "generic"
    max_input_tokens: int = Field(default=4096, ge=1, le=131072)
    max_new_tokens: int = Field(default=1024, ge=1, le=32768)
    top_p: float = Field(default=1.0, gt=0, le=1)
    queue_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    inference_timeout_seconds: float = Field(default=120, gt=0, le=3600)
    max_queue_size: int = Field(default=8, ge=0, le=1024)
    max_batch_inputs: int = Field(default=1024, ge=1, le=65536)
    max_input_characters: int = Field(default=262144, ge=1, le=16777216)

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        if value in {"auto", "cpu", "cuda"}:
            return value
        if value.startswith("cuda:") and value[5:].isascii() and value[5:].isdigit():
            return f"cuda:{int(value[5:])}"
        raise ValueError("Local device must be auto, cpu, cuda or cuda:<nonnegative index>")

    @field_validator("revision", "tokenizer_revision", "cache_dir")
    @classmethod
    def validate_nonempty(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value or any(ord(character) < 32 for character in value):
                raise ValueError(
                    "Model revisions and paths must be nonempty without control characters"
                )
        return value
