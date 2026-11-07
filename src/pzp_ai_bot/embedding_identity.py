from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from pzp_ai_bot.ai.factory import ProviderSelection
    from pzp_ai_bot.config import ProviderProfile


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(value: Any) -> str:
    return content_hash(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def embedding_configuration(profile: ProviderProfile, model: str) -> dict[str, Any]:
    """Describe embedding semantics without credentials or machine-local settings."""
    options = profile.local_embedding_options
    endpoint = None
    if profile.provider == "openai_compatible":
        parsed = urlsplit(profile.openai_base_url)
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError("embedding_endpoint_invalid")
        host = (parsed.hostname or "").lower()
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        if port is not None and not (
            (parsed.scheme.lower() == "https" and port == 443)
            or (parsed.scheme.lower() == "http" and port == 80)
        ):
            host = f"{host}:{port}"
        endpoint = urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/"), "", ""))
    semantic = {
        "identity_version": 1,
        "provider_kind": profile.provider,
        "backend": "sentence_transformers" if profile.provider == "local" else profile.provider,
        "endpoint": endpoint,
        "endpoint_query_hash": _fingerprint(urlsplit(profile.openai_base_url).query)
        if endpoint
        else None,
        "model": model,
        "revision": options.get("revision"),
        "tokenizer_revision": options.get("tokenizer_revision"),
        "normalize_embeddings": options.get("normalize_embeddings", True)
        if profile.provider == "local"
        else False,
        "truncate_dim": options.get("truncate_dim"),
        "prompt_mode": options.get("prompt_mode", "generic"),
        "query_prompt_hash": _fingerprint(options.get("query_prompt")),
        "document_prompt_hash": _fingerprint(options.get("document_prompt")),
        "redact_before_ai": profile.redact_before_ai,
        "redaction_patterns_hash": _fingerprint(profile.redaction_custom_patterns),
        "preprocessing_version": "pzp-source-v1",
        "fake_embedding_dimensions": profile.fake_embedding_dimensions
        if profile.provider == "fake"
        else None,
    }
    return {"config_hash": _fingerprint(semantic), "config_json": semantic}


def embedding_space_metadata(selection: ProviderSelection, dimension: int) -> dict[str, Any]:
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
        raise ValueError("embedding_dimension_invalid")
    configuration = embedding_configuration(selection.profile, selection.embedding_model)
    return {
        "key": _fingerprint({"config_hash": configuration["config_hash"], "dimension": dimension}),
        "provider_profile": selection.profile.name,
        "provider_kind": selection.profile.provider,
        "model": selection.embedding_model,
        "profile_embedding_model": selection.profile.embedding_model,
        "managed_profile": selection.managed_profile,
        "profile_revision": selection.profile_revision,
        "dimension": dimension,
        **configuration,
        "created_at": datetime.now(UTC).isoformat(),
    }
