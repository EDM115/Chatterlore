from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.ai.factory import ProviderRegistry
from pzp_ai_bot.config import ProviderKind, ProviderProfile, Settings
from pzp_ai_bot.db.models import AIProviderCredential, AIProviderProfile
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.provider_credentials import (
    ProviderCredentialRepository,
    ProviderProfileRepository,
)
from pzp_ai_bot.db.repos.shared import normalize_profile_name
from pzp_ai_bot.db.session import session_scope

from .crypto import EncryptedProviderSecret, MasterKeyring, ProviderSecretVault, SecretVaultError


class ProviderSecretConflictError(ValueError):
    def __init__(self, message: str, *, current_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version


@dataclass(frozen=True, slots=True)
class ProviderProfileSummary:
    name: str
    provider_kind: str
    base_url: str
    chat_model: str
    embedding_model: str
    chat_capable: bool
    embedding_capable: bool
    reasoning_capable: bool
    local: bool
    redact_before_ai: bool
    redaction_patterns: tuple[str, ...]
    fake_embedding_dimensions: int
    enabled: bool
    source: str
    version: int
    audit_event_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("audit_event_id")
        return payload


@dataclass(frozen=True, slots=True)
class ProviderCredentialSummary:
    id: int | str
    profile_name: str
    label: str
    source: str
    environment_variable: str | None
    last_four: str | None
    fingerprint: str | None
    position: int
    state: str
    cooldown_until: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    version: int
    revealable: bool
    audit_event_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("audit_event_id")
        return payload


@dataclass(frozen=True, slots=True)
class ProviderSecretStatus:
    configured_key_ids: tuple[str, ...]
    active_key_id: str
    encrypted_credentials: int
    credentials_by_key_id: dict[str, int]
    missing_key_ids: tuple[str, ...]


class ProviderSecretService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        registry: ProviderRegistry,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry
        self.vault = ProviderSecretVault(
            MasterKeyring(
                settings.control_plane_master_key_bytes, settings.control_plane_active_master_key_id
            )
        )

    async def refresh_registry(self) -> None:
        bootstrap = self.settings.provider_profiles
        managed: dict[str, ProviderProfile] = {}
        async with session_scope(self.session_factory) as session:
            profiles = await ProviderProfileRepository(session).list_all()
            credentials = ProviderCredentialRepository(session)
            for stored in profiles:
                if not stored.enabled:
                    continue
                bootstrap_profile = bootstrap.get(stored.name)
                api_key = bootstrap_profile.openai_api_key if bootstrap_profile else ""
                api_key_env = bootstrap_profile.openai_api_key_env if bootstrap_profile else None
                for credential in await credentials.list_for_profile(stored.id):
                    if (
                        credential.lifecycle_state == "enabled"
                        and credential.secret_ciphertext is not None
                    ):
                        api_key = self.vault.decrypt(
                            _encrypted(credential),
                            profile_name=stored.name,
                            credential_id=credential.id,
                        )
                        api_key_env = None
                        break
                managed[stored.name] = _runtime_profile(
                    stored, api_key=api_key, api_key_env=api_key_env
                )
        self.registry.replace_managed_profiles(managed)

    async def list_profiles(self) -> list[ProviderProfileSummary]:
        bootstrap = {
            name: _bootstrap_profile_summary(profile)
            for name, profile in self.settings.provider_profiles.items()
        }
        async with session_scope(self.session_factory) as session:
            for stored in await ProviderProfileRepository(session).list_all():
                bootstrap[stored.name] = _profile_summary(stored)
        return [bootstrap[name] for name in sorted(bootstrap)]

    async def get_profile(self, name: str) -> ProviderProfileSummary:
        clean = normalize_profile_name(name)
        async with session_scope(self.session_factory) as session:
            stored = await ProviderProfileRepository(session).get_by_name(clean)
        if stored is not None:
            return _profile_summary(stored)
        bootstrap = self.settings.provider_profiles.get(clean)
        if bootstrap is None:
            raise ValueError("provider profile was not found")
        return _bootstrap_profile_summary(bootstrap)

    async def create_profile(
        self, payload: dict[str, Any], *, actor_user_id: int, now: datetime
    ) -> ProviderProfileSummary:
        values = _validated_profile_values(payload)
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            if await profiles.count() >= self.settings.control_plane_max_managed_profiles:
                raise ValueError("managed provider profile limit was reached")
            if await profiles.get_by_name(values["name"]) is not None:
                raise ValueError("managed provider profile already exists")
            stored = await profiles.create(**values, actor_user_id=actor_user_id, now=now)
            audit_event_id = await _audit(
                session,
                "created",
                actor_user_id,
                {"profile": stored.name, "profile_version": stored.version},
            )
            result = replace(_profile_summary(stored), audit_event_id=audit_event_id)
        await self.refresh_registry()
        return result

    async def update_profile(
        self,
        name: str,
        *,
        expected_version: int,
        updates: dict[str, Any],
        actor_user_id: int,
        now: datetime,
    ) -> ProviderProfileSummary:
        clean = normalize_profile_name(name)
        values = _validated_profile_updates(updates)
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            existing = await profiles.get_by_name(clean)
            if existing is None:
                raise ValueError("managed provider profile was not found")
            stored = await profiles.update(
                existing.id,
                expected_version=expected_version,
                updates=values,
                actor_user_id=actor_user_id,
                now=now,
            )
            if stored is None:
                current = await profiles.get_by_id(existing.id)
                raise ProviderSecretConflictError(
                    "provider profile changed concurrently",
                    current_version=current.version if current else None,
                )
            audit_event_id = await _audit(
                session,
                "updated",
                actor_user_id,
                {"profile": stored.name, "profile_version": stored.version},
            )
            result = replace(_profile_summary(stored), audit_event_id=audit_event_id)
        await self.refresh_registry()
        return result

    async def delete_profile(self, name: str, *, expected_version: int, actor_user_id: int) -> int:
        clean = normalize_profile_name(name)
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            existing = await profiles.get_by_name(clean)
            if existing is None:
                raise ValueError("managed provider profile was not found")
            deleted = await profiles.delete(existing.id, expected_version=expected_version)
            if deleted is None:
                raise ProviderSecretConflictError(
                    "provider profile changed concurrently", current_version=existing.version
                )
            audit_event_id = await _audit(
                session,
                "deleted",
                actor_user_id,
                {"profile": clean, "profile_version": expected_version},
            )
        await self.refresh_registry()
        return audit_event_id

    async def list_credentials(self, name: str) -> list[ProviderCredentialSummary]:
        clean = normalize_profile_name(name)
        output: list[ProviderCredentialSummary] = []
        bootstrap = self.settings.provider_profiles.get(clean)
        if bootstrap and bootstrap.openai_api_key:
            key_id = self.settings.control_plane_active_master_key_id
            output.append(
                ProviderCredentialSummary(
                    id=f"env:{bootstrap.openai_api_key_env or 'inline'}",
                    profile_name=clean,
                    label=bootstrap.openai_api_key_env or "Bootstrap credential",
                    source="environment",
                    environment_variable=bootstrap.openai_api_key_env,
                    last_four=bootstrap.openai_api_key[-4:],
                    fingerprint=self.vault.fingerprint(bootstrap.openai_api_key, key_id=key_id),
                    position=-1,
                    state="enabled",
                    cooldown_until=None,
                    last_success_at=None,
                    last_failure_at=None,
                    version=0,
                    revealable=False,
                )
            )
        async with session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(clean)
            if profile is None:
                if bootstrap is None:
                    raise ValueError("provider profile was not found")
                return output
            credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
            output.extend(_credential_summary(profile.name, item) for item in credentials)
        return output

    async def create_credential(
        self, name: str, *, label: str, value: str, actor_user_id: int, now: datetime
    ) -> ProviderCredentialSummary:
        clean = normalize_profile_name(name)
        safe_label = _normalize_label(label)
        if not value or len(value) > self.settings.control_plane_max_secret_length:
            raise ValueError("provider credential value is invalid")
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            profile = await profiles.get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            credentials = ProviderCredentialRepository(session)
            count = await credentials.count_for_profile(profile.id)
            if count >= self.settings.control_plane_max_credentials_per_profile:
                raise ValueError("provider credential limit was reached")
            stored = await credentials.create_provisioning(
                profile_id=profile.id,
                label=safe_label,
                position=count,
                actor_user_id=actor_user_id,
                now=now,
            )
            encrypted = self.vault.encrypt(
                value, profile_name=profile.name, credential_id=stored.id
            )
            stored = await credentials.store_encrypted(
                stored.id,
                ciphertext=encrypted.ciphertext,
                secret_nonce=encrypted.secret_nonce,
                wrapped_dek=encrypted.wrapped_dek,
                wrap_nonce=encrypted.wrap_nonce,
                master_key_id=encrypted.master_key_id,
                fingerprint=encrypted.fingerprint,
                last_four=encrypted.last_four,
            )
            audit_event_id = await _audit(
                session,
                "credential_created",
                actor_user_id,
                {
                    "profile": profile.name,
                    "credential_id": stored.id,
                    "credential_version": stored.version,
                },
            )
            result = replace(
                _credential_summary(profile.name, stored), audit_event_id=audit_event_id
            )
        await self.refresh_registry()
        return result

    async def update_credential(
        self,
        name: str,
        credential_id: int,
        *,
        expected_version: int,
        updates: dict[str, Any],
        actor_user_id: int,
        now: datetime,
    ) -> ProviderCredentialSummary:
        clean = normalize_profile_name(name)
        values = _validated_credential_updates(updates)
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            profile = await profiles.get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            credentials = ProviderCredentialRepository(session)
            existing = await credentials.get(credential_id)
            if existing is None or existing.profile_id != profile.id:
                raise ValueError("provider credential was not found")
            stored = await credentials.update(
                credential_id,
                expected_version=expected_version,
                updates=values,
                actor_user_id=actor_user_id,
                now=now,
            )
            if stored is None:
                current = await credentials.get(credential_id)
                raise ProviderSecretConflictError(
                    "provider credential changed concurrently",
                    current_version=current.version if current else None,
                )
            audit_event_id = await _audit(
                session,
                "credential_updated",
                actor_user_id,
                {
                    "profile": clean,
                    "credential_id": stored.id,
                    "credential_version": stored.version,
                },
            )
            result = replace(_credential_summary(clean, stored), audit_event_id=audit_event_id)
        await self.refresh_registry()
        return result

    async def delete_credential(
        self,
        name: str,
        credential_id: int,
        *,
        expected_version: int,
        actor_user_id: int,
        now: datetime,
    ) -> int:
        clean = normalize_profile_name(name)
        async with session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            credentials = ProviderCredentialRepository(session)
            existing = await credentials.get(credential_id)
            if existing is None or existing.profile_id != profile.id:
                raise ValueError("provider credential was not found")
            deleted = await credentials.tombstone(
                credential_id,
                expected_version=expected_version,
                actor_user_id=actor_user_id,
                now=now,
            )
            if deleted is None:
                current = await credentials.get(credential_id)
                raise ProviderSecretConflictError(
                    "provider credential changed concurrently",
                    current_version=current.version if current else None,
                )
            audit_event_id = await _audit(
                session,
                "credential_deleted",
                actor_user_id,
                {
                    "profile": clean,
                    "credential_id": credential_id,
                    "credential_version": deleted.version,
                },
            )
        await self.refresh_registry()
        return audit_event_id

    async def reveal_credential(self, name: str, credential_id: int, *, actor_user_id: int) -> str:
        clean = normalize_profile_name(name)
        async with session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            stored = await ProviderCredentialRepository(session).get(credential_id)
            if (
                stored is None
                or stored.profile_id != profile.id
                or stored.lifecycle_state == "deleted"
                or stored.secret_ciphertext is None
            ):
                raise ValueError("provider credential was not found")
            value = self.vault.decrypt(
                _encrypted(stored), profile_name=profile.name, credential_id=stored.id
            )
            await _audit(
                session,
                "credential_revealed",
                actor_user_id,
                {
                    "profile": clean,
                    "credential_id": credential_id,
                    "credential_version": stored.version,
                },
            )
            return value

    async def status(self) -> ProviderSecretStatus:
        counts: dict[str, int] = {}
        async with session_scope(self.session_factory) as session:
            credentials = await ProviderCredentialRepository(session).list_encrypted()
            for stored in credentials:
                key_id = stored.master_key_id or "missing"
                counts[key_id] = counts.get(key_id, 0) + 1
        configured = tuple(sorted(self.vault.keyring.keys))
        return ProviderSecretStatus(
            configured_key_ids=configured,
            active_key_id=self.vault.keyring.active_key_id,
            encrypted_credentials=sum(counts.values()),
            credentials_by_key_id=dict(sorted(counts.items())),
            missing_key_ids=tuple(sorted(key_id for key_id in counts if key_id not in configured)),
        )

    async def rewrap(self, target_key_id: str, *, actor_user_id: int | None = None) -> int:
        self.vault.keyring.key(target_key_id)
        async with session_scope(self.session_factory) as session:
            profiles = {
                profile.id: profile
                for profile in await ProviderProfileRepository(session).list_all()
            }
            credentials = await ProviderCredentialRepository(session).list_encrypted()
            replacements: list[tuple[int, EncryptedProviderSecret]] = []
            for stored in credentials:
                profile = profiles.get(stored.profile_id)
                if profile is None:
                    raise SecretVaultError("provider credential cannot be rewrapped")
                rewrapped = self.vault.rewrap(
                    _encrypted(stored),
                    profile_name=profile.name,
                    credential_id=stored.id,
                    target_key_id=target_key_id,
                )
                replacements.append((stored.id, rewrapped))
            repository = ProviderCredentialRepository(session)
            for credential_id, rewrapped in replacements:
                await repository.replace_wrapping(
                    credential_id,
                    wrapped_dek=rewrapped.wrapped_dek,
                    wrap_nonce=rewrapped.wrap_nonce,
                    master_key_id=rewrapped.master_key_id,
                )
            await EventRepository(session).add(
                event_type="control_plane.provider.credentials_rewrapped",
                user_id=actor_user_id,
                payload={"target_key_id": target_key_id, "credential_count": len(replacements)},
            )
        await self.refresh_registry()
        return len(replacements)


def _runtime_profile(
    stored: AIProviderProfile, *, api_key: str, api_key_env: str | None
) -> ProviderProfile:
    return ProviderProfile(
        name=stored.name,
        provider=cast(ProviderKind, stored.provider_kind),
        chat_model=stored.chat_model,
        embedding_model=stored.embedding_model,
        openai_api_key=api_key,
        openai_api_key_env=api_key_env,
        openai_base_url=stored.base_url,
        redact_before_ai=stored.redact_before_ai,
        redaction_custom_patterns=tuple(stored.redaction_patterns),
        fake_embedding_dimensions=stored.fake_embedding_dimensions,
    )


def _profile_summary(stored: AIProviderProfile) -> ProviderProfileSummary:
    return ProviderProfileSummary(
        name=stored.name,
        provider_kind=stored.provider_kind,
        base_url=stored.base_url,
        chat_model=stored.chat_model,
        embedding_model=stored.embedding_model,
        chat_capable=stored.chat_capable,
        embedding_capable=stored.embedding_capable,
        reasoning_capable=stored.reasoning_capable,
        local=stored.local,
        redact_before_ai=stored.redact_before_ai,
        redaction_patterns=tuple(stored.redaction_patterns),
        fake_embedding_dimensions=stored.fake_embedding_dimensions,
        enabled=stored.enabled,
        source="managed",
        version=stored.version,
    )


def _bootstrap_profile_summary(profile: ProviderProfile) -> ProviderProfileSummary:
    return ProviderProfileSummary(
        name=profile.name,
        provider_kind=profile.provider,
        base_url=profile.openai_base_url,
        chat_model=profile.chat_model,
        embedding_model=profile.embedding_model,
        chat_capable=True,
        embedding_capable=True,
        reasoning_capable=profile.provider == "openai_compatible",
        local=profile.provider == "local",
        redact_before_ai=profile.redact_before_ai,
        redaction_patterns=profile.redaction_custom_patterns,
        fake_embedding_dimensions=profile.fake_embedding_dimensions,
        enabled=True,
        source="environment",
        version=0,
    )


def _credential_summary(
    profile_name: str, stored: AIProviderCredential
) -> ProviderCredentialSummary:
    return ProviderCredentialSummary(
        id=stored.id,
        profile_name=profile_name,
        label=stored.label,
        source=stored.source_kind,
        environment_variable=stored.environment_variable,
        last_four=stored.last_four,
        fingerprint=stored.fingerprint,
        position=stored.position,
        state=stored.lifecycle_state,
        cooldown_until=stored.cooldown_until,
        last_success_at=stored.last_success_at,
        last_failure_at=stored.last_failure_at,
        version=stored.version,
        revealable=stored.source_kind == "managed"
        and stored.lifecycle_state != "deleted"
        and stored.secret_ciphertext is not None,
    )


def _encrypted(stored: AIProviderCredential) -> EncryptedProviderSecret:
    ciphertext = stored.secret_ciphertext
    secret_nonce = stored.secret_nonce
    wrapped_dek = stored.wrapped_dek
    wrap_nonce = stored.wrap_nonce
    master_key_id = stored.master_key_id
    fingerprint = stored.fingerprint
    last_four = stored.last_four
    if (
        ciphertext is None
        or secret_nonce is None
        or wrapped_dek is None
        or wrap_nonce is None
        or master_key_id is None
        or fingerprint is None
        or last_four is None
    ):
        raise SecretVaultError("provider credential cannot be decrypted")
    return EncryptedProviderSecret(
        ciphertext=ciphertext,
        secret_nonce=secret_nonce,
        wrapped_dek=wrapped_dek,
        wrap_nonce=wrap_nonce,
        master_key_id=master_key_id,
        fingerprint=fingerprint,
        last_four=last_four,
    )


def _validated_profile_values(payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "name",
        "provider_kind",
        "base_url",
        "chat_model",
        "embedding_model",
        "chat_capable",
        "embedding_capable",
        "reasoning_capable",
        "local",
        "redact_before_ai",
        "redaction_patterns",
        "fake_embedding_dimensions",
    }
    if not required <= payload.keys():
        raise ValueError("managed provider profile is incomplete")
    values = {key: payload[key] for key in required}
    values["name"] = normalize_profile_name(str(values["name"]))
    if not values["name"]:
        raise ValueError("provider profile name cannot be empty")
    values.update(_validated_profile_updates(values))
    return values


def _validated_profile_updates(updates: dict[str, Any]) -> dict[str, Any]:
    values = dict(updates)
    if "provider_kind" in values and values["provider_kind"] not in {
        "fake",
        "openai_compatible",
        "local",
    }:
        raise ValueError("provider kind is invalid")
    for field in ("base_url", "chat_model", "embedding_model"):
        if field in values:
            values[field] = str(values[field]).strip()
            if not values[field]:
                raise ValueError(f"{field} cannot be empty")
    if "base_url" in values:
        parsed = urlsplit(values["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("provider base URL is invalid")
    if "redaction_patterns" in values:
        if not isinstance(values["redaction_patterns"], list):
            raise ValueError("redaction patterns must be a list")
        values["redaction_patterns"] = [
            str(pattern).strip() for pattern in values["redaction_patterns"] if str(pattern).strip()
        ]
    if "fake_embedding_dimensions" in values:
        dimensions = int(values["fake_embedding_dimensions"])
        if dimensions < 8 or dimensions > 65_536:
            raise ValueError("fake embedding dimensions are invalid")
        values["fake_embedding_dimensions"] = dimensions
    return values


def _validated_credential_updates(updates: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "label" in updates:
        values["label"] = _normalize_label(str(updates["label"]))
    if "position" in updates:
        position = int(updates["position"])
        if position < 0 or position > 10_000:
            raise ValueError("credential position is invalid")
        values["position"] = position
    if "state" in updates:
        if updates["state"] not in {"enabled", "disabled"}:
            raise ValueError("credential state is invalid")
        values["lifecycle_state"] = updates["state"]
    return values


def _normalize_label(value: str) -> str:
    label = " ".join(value.split())
    if not label or len(label) > 64:
        raise ValueError("provider credential label is invalid")
    return label


async def _audit(
    session: AsyncSession, action: str, actor_user_id: int, payload: dict[str, Any]
) -> int:
    event = await EventRepository(session).add(
        event_type=f"control_plane.provider.{action}", user_id=actor_user_id, payload=payload
    )
    return event.id
