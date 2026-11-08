from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LegacyEmbeddingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    embedding_id: int = Field(strict=True, ge=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    vector_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class LegacyAdoptionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: str = Field(min_length=1, max_length=128)
    provider_profile: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    model: str = Field(min_length=1, max_length=256)
    dimension: int = Field(strict=True, ge=1, le=65_536)
    original_config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: tuple[LegacyEmbeddingEvidence, ...] = Field(min_length=1, max_length=100)

    @classmethod
    def parse(cls, value: Any) -> LegacyAdoptionManifest:
        try:
            manifest = cls.model_validate(value)
        except ValidationError:
            raise ValueError("embedding_adoption_manifest_invalid") from None
        if len({entry.embedding_id for entry in manifest.entries}) != len(manifest.entries):
            raise ValueError("embedding_adoption_manifest_invalid")
        return manifest
