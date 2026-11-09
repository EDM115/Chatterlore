from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chatterlore.ai.factory import ProviderRegistry
from chatterlore.config import (
    APIDialect,
    AuthMode,
    ProviderKind,
    ProviderProfile,
    Settings,
    _local_options,
)
from chatterlore.db.models import AIProviderCredential, AIProviderProfile
from chatterlore.db.repos.embedding_indexes import EmbeddingIndexRepository
from chatterlore.db.repos.events import EventRepository
from chatterlore.db.repos.provider_credentials import (
    ProviderCredentialRepository,
    ProviderPoolStateRepository,
    ProviderProfileRepository,
)
from chatterlore.db.repos.settings import RuntimeSettingRepository
from chatterlore.db.repos.shared import normalize_profile_name
from chatterlore.db.session import session_scope

from .crypto import EncryptedProviderSecret, MasterKeyring, ProviderSecretVault, SecretVaultError


class ProviderSecretConflictError(ValueError):
    def __init__(self, message: str, *, current_version: int | None = None) -> None:
        super().__init__(message)
        self.current_version = current_version


class ProviderProfileDependencyError(ValueError):
    def __init__(self, *, deleting: bool = False, setting: str | None = None) -> None:
        if setting is not None:
            self.code = "profile_in_use"
            super().__init__(
                f"{self.code}: reassign {setting} before disabling or deleting this provider"
            )
            return
        self.code = "profile_in_use" if deleting else "embedding_migration_required"
        super().__init__(
            f"{self.code}: profile is in use, create another profile and migrate its index first"
        )


_EMBEDDING_PROFILE_FIELDS = {
    "provider_kind",
    "base_url",
    "embedding_model",
    "local",
    "local_embedding_options",
    "redact_before_ai",
    "redaction_patterns",
    "fake_embedding_dimensions",
    "embedding_capable",
}


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
    api_dialect: str = "auto"
    auth_mode: str = "api_key"
    local_chat_options: dict[str, Any] = field(default_factory=dict)
    local_embedding_options: dict[str, Any] = field(default_factory=dict)

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
    health_state: str
    cooldown_until: datetime | None
    last_error_classification: str | None
    success_count: int
    failure_count: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    version: int
    revealable: bool
    active: bool
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
        self._vault: ProviderSecretVault | None = None

    @property
    def vault(self) -> ProviderSecretVault:
        if self._vault is None:
            self._vault = ProviderSecretVault(
                MasterKeyring(
                    self.settings.control_plane_master_key_bytes,
                    self.settings.control_plane_active_master_key_id,
                )
            )
        return self._vault

    async def refresh_registry(self) -> None:
        bootstrap = self.settings.provider_profiles
        managed: dict[str, ProviderProfile] = {}
        revisions: dict[str, int] = {}
        capabilities: dict[str, frozenset[str]] = {}
        disabled: set[str] = set()
        async with session_scope(self.session_factory) as session:
            profiles = await ProviderProfileRepository(session).list_all()
            credentials = ProviderCredentialRepository(session)
            for stored in profiles:
                if not stored.enabled:
                    disabled.add(stored.name)
                    continue
                bootstrap_profile = bootstrap.get(stored.name)
                allow_bootstrap = bool(
                    bootstrap_profile
                    and stored.auth_mode != "none"
                    and bootstrap_profile.openai_base_url.rstrip("/") == stored.base_url.rstrip("/")
                    and (not stored.local or bootstrap_profile.local)
                )
                api_key = (
                    bootstrap_profile.openai_api_key
                    if allow_bootstrap and bootstrap_profile
                    else ""
                )
                api_key_env = (
                    bootstrap_profile.openai_api_key_env
                    if allow_bootstrap and bootstrap_profile
                    else None
                )
                if api_key:
                    key_id = self.settings.control_plane_active_master_key_id
                    await credentials.sync_environment(
                        profile_id=stored.id,
                        label=api_key_env or "Bootstrap credential",
                        environment_variable=api_key_env,
                        fingerprint=self.vault.fingerprint(api_key, key_id=key_id),
                        master_key_id=key_id,
                        now=datetime.now(UTC),
                    )
                else:
                    detached = await credentials.retire_environment(
                        stored.id, now=datetime.now(UTC)
                    )
                    for credential_id in detached:
                        await _clear_active_credential(session, stored.id, credential_id)
                    if detached:
                        await _audit(
                            session,
                            "environment_credential_detached",
                            0,
                            {"profile": stored.name, "credential_ids": detached},
                        )
                managed[stored.name] = _runtime_profile(
                    stored, api_key=api_key, api_key_env=api_key_env
                )
                revisions[stored.name] = stored.version
                profile_capabilities: set[str] = set()
                if stored.chat_capable and stored.chat_model:
                    profile_capabilities.add("chat")
                if stored.embedding_capable and stored.embedding_model:
                    profile_capabilities.add("embedding")
                from chatterlore.ai.openai_compatible import (
                    resolve_api_dialect,
                    supports_reasoning_effort,
                )

                dialect = resolve_api_dialect(
                    stored.api_dialect,
                    base_url=stored.base_url,
                    model=stored.chat_model,
                    local=stored.local,
                )
                if (
                    stored.reasoning_capable
                    and stored.provider_kind == "openai_compatible"
                    and "chat" in profile_capabilities
                    and (
                        dialect == "deepseek"
                        or (dialect == "openai" and supports_reasoning_effort(stored.chat_model))
                    )
                ):
                    profile_capabilities.add("reasoning")
                capabilities[stored.name] = frozenset(profile_capabilities)
        self.registry.replace_managed_profiles(
            managed, revisions=revisions, capabilities=capabilities, disabled=disabled
        )

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
        self._validate_deployment_profile(values)
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            if await profiles.count() >= self.settings.control_plane_max_managed_profiles:
                raise ValueError("managed provider profile limit was reached")
            if await profiles.get_by_name(values["name"]) is not None:
                raise ValueError("managed provider profile already exists")
            bootstrap = self.settings.provider_profiles.get(values["name"])
            if bootstrap is not None:
                previous = _bootstrap_profile_summary(bootstrap).as_dict()
                previous["redaction_patterns"] = list(previous["redaction_patterns"])
                if any(previous[key] != values[key] for key in _EMBEDDING_PROFILE_FIELDS):
                    await EmbeddingIndexRepository(session).advance_epoch()
                    if await profiles.has_embedding_dependencies(values["name"]):
                        raise ProviderProfileDependencyError()
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
            combined = {**_profile_summary(existing).as_dict(), **values}
            _validate_profile_capability_models(combined)
            self._validate_deployment_profile(combined)
            embedding_changed = any(
                key in values and values[key] != getattr(existing, key)
                for key in _EMBEDDING_PROFILE_FIELDS
            )
            disabling = values.get("enabled") is False and existing.enabled
            if embedding_changed or disabling:
                await EmbeddingIndexRepository(session).advance_epoch()
                if await profiles.has_embedding_dependencies(clean):
                    raise ProviderProfileDependencyError()
                if disabling:
                    await self._validate_profile_removal(session, clean)
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

    async def _validate_profile_removal(
        self, session: AsyncSession, name: str, *, deleting: bool = False
    ) -> None:
        if deleting and name in self.settings.provider_profiles:
            return
        if name == self.settings.ai_provider_profile.strip().lower():
            raise ProviderProfileDependencyError(setting="deployment AI_PROVIDER_PROFILE")
        runtime = await RuntimeSettingRepository(session).values()
        for operation in ("chat", "summary", "embedding"):
            key = f"{operation}_provider_profile"
            if key not in runtime and name == getattr(self.settings, f"ai_{key}").strip().lower():
                raise ProviderProfileDependencyError(setting=f"deployment AI_{key.upper()}")
        if await ProviderProfileRepository(session).has_settings_dependencies(name):
            raise ProviderProfileDependencyError(setting="the runtime or scoped provider settings")

    def _validate_deployment_profile(self, values: dict[str, Any]) -> None:
        if values.get("provider_kind") == "openai_compatible":
            parsed = urlsplit(values["base_url"])
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or parsed.query
            ):
                raise ValueError("provider base URL is invalid")
        if (
            values.get("auth_mode") == "none"
            and not values.get("local")
            and values.get("provider_kind") != "local"
        ):
            raise ValueError("auth_mode=none requires an explicitly local provider")
        if values.get("local") and values.get("provider_kind") == "openai_compatible":
            self.settings.validate_local_server_url(str(values["base_url"]))
        if values.get("provider_kind") == "local":
            if values.get("chat_capable"):
                self.settings.validate_local_paths(
                    str(values["chat_model"]), values.get("local_chat_options", {})
                )
            if values.get("embedding_capable"):
                self.settings.validate_local_paths(
                    str(values["embedding_model"]), values.get("local_embedding_options", {})
                )

    async def delete_profile(self, name: str, *, expected_version: int, actor_user_id: int) -> int:
        clean = normalize_profile_name(name)
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            existing = await profiles.get_by_name(clean)
            if existing is None:
                raise ValueError("managed provider profile was not found")
            await EmbeddingIndexRepository(session).advance_epoch()
            await self._validate_profile_removal(session, clean, deleting=True)
            if await profiles.has_embedding_dependencies(clean):
                raise ProviderProfileDependencyError(deleting=True)
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
        bootstrap = self.settings.provider_profiles.get(clean)
        async with session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(clean)
            if profile is None:
                if bootstrap is None:
                    raise ValueError("provider profile was not found")
                if not bootstrap.openai_api_key:
                    return []
                key_id = self.settings.control_plane_active_master_key_id
                return [
                    ProviderCredentialSummary(
                        id=f"env:{bootstrap.openai_api_key_env or 'inline'}",
                        profile_name=clean,
                        label=bootstrap.openai_api_key_env or "Bootstrap credential",
                        source="environment",
                        environment_variable=bootstrap.openai_api_key_env,
                        last_four=None,
                        fingerprint=self.vault.fingerprint(bootstrap.openai_api_key, key_id=key_id),
                        position=-1,
                        state="enabled",
                        health_state="unknown",
                        cooldown_until=None,
                        last_error_classification=None,
                        success_count=0,
                        failure_count=0,
                        last_success_at=None,
                        last_failure_at=None,
                        version=0,
                        revealable=False,
                        active=False,
                    )
                ]
            credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
            pool_state = await ProviderProfileRepository(session).get_pool_state(profile.id)
            active_id = pool_state.active_credential_id if pool_state is not None else None
            return [
                _credential_summary(profile.name, item, active_id=active_id) for item in credentials
            ]

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
        if values.get("lifecycle_state") == "enabled":
            values.update(
                health_state="unknown", cooldown_until=None, last_error_classification=None
            )
        elif values.get("lifecycle_state") == "disabled":
            values.update(
                health_state="disabled", cooldown_until=None, last_error_classification="manual"
            )
        async with session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            profile = await profiles.get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            credentials = ProviderCredentialRepository(session)
            existing = await credentials.get(credential_id)
            if existing is None or existing.profile_id != profile.id:
                raise ValueError("provider credential was not found")
            if existing.source_kind == "environment" and {"label", "position"}.intersection(values):
                raise ValueError("environment provider credentials are immutable")
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
            if stored.lifecycle_state != "enabled" or stored.health_state == "disabled":
                await _clear_active_credential(session, profile.id, stored.id)
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

    async def replace_credential(
        self,
        name: str,
        credential_id: int,
        *,
        expected_version: int,
        value: str,
        label: str | None,
        actor_user_id: int,
        now: datetime,
    ) -> ProviderCredentialSummary:
        clean = normalize_profile_name(name)
        if not value or len(value) > self.settings.control_plane_max_secret_length:
            raise ValueError("provider credential value is invalid")
        safe_label = _normalize_label(label) if label is not None else None
        async with session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            credentials = ProviderCredentialRepository(session)
            existing = await credentials.get(credential_id)
            if existing is None or existing.profile_id != profile.id:
                raise ValueError("provider credential was not found")
            if existing.source_kind != "managed":
                raise ValueError("environment provider credentials are immutable")
            encrypted = self.vault.encrypt(
                value, profile_name=profile.name, credential_id=existing.id
            )
            stored = await credentials.replace_secret(
                credential_id,
                expected_version=expected_version,
                ciphertext=encrypted.ciphertext,
                secret_nonce=encrypted.secret_nonce,
                wrapped_dek=encrypted.wrapped_dek,
                wrap_nonce=encrypted.wrap_nonce,
                master_key_id=encrypted.master_key_id,
                fingerprint=encrypted.fingerprint,
                last_four=encrypted.last_four,
                label=safe_label,
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
                "credential_replaced",
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
            if existing.source_kind == "environment":
                raise ValueError("environment provider credentials are immutable")
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
            await _clear_active_credential(session, profile.id, deleted.id)
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
        api_dialect=cast(APIDialect, stored.api_dialect),
        auth_mode=cast(AuthMode, stored.auth_mode),
        local=stored.local or stored.provider_kind == "local",
        chat_capable=stored.chat_capable,
        embedding_capable=stored.embedding_capable,
        reasoning_capable=stored.reasoning_capable,
        local_chat_options=dict(stored.local_chat_options),
        local_embedding_options=dict(stored.local_embedding_options),
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
        api_dialect=stored.api_dialect,
        auth_mode=stored.auth_mode,
        local_chat_options=dict(stored.local_chat_options),
        local_embedding_options=dict(stored.local_embedding_options),
    )


def _bootstrap_profile_summary(profile: ProviderProfile) -> ProviderProfileSummary:
    return ProviderProfileSummary(
        name=profile.name,
        provider_kind=profile.provider,
        base_url=profile.openai_base_url,
        chat_model=profile.chat_model,
        embedding_model=profile.embedding_model,
        chat_capable=profile.chat_capable,
        embedding_capable=profile.embedding_capable,
        reasoning_capable=profile.reasoning_capable,
        local=profile.local or profile.provider == "local",
        redact_before_ai=profile.redact_before_ai,
        redaction_patterns=profile.redaction_custom_patterns,
        fake_embedding_dimensions=profile.fake_embedding_dimensions,
        enabled=True,
        source="environment",
        version=0,
        api_dialect=profile.api_dialect,
        auth_mode=profile.auth_mode,
        local_chat_options=dict(profile.local_chat_options),
        local_embedding_options=dict(profile.local_embedding_options),
    )


def _credential_summary(
    profile_name: str, stored: AIProviderCredential, *, active_id: int | None = None
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
        health_state=stored.health_state,
        cooldown_until=stored.cooldown_until,
        last_error_classification=stored.last_error_classification,
        success_count=stored.success_count,
        failure_count=stored.failure_count,
        last_success_at=stored.last_success_at,
        last_failure_at=stored.last_failure_at,
        version=stored.version,
        revealable=stored.source_kind == "managed"
        and stored.lifecycle_state != "deleted"
        and stored.secret_ciphertext is not None,
        active=stored.id == active_id
        and stored.lifecycle_state == "enabled"
        and stored.health_state != "disabled",
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
    for key, default in {
        "api_dialect": "auto",
        "auth_mode": "api_key",
        "local_chat_options": {},
        "local_embedding_options": {},
    }.items():
        values[key] = payload.get(key, default)
    values["name"] = normalize_profile_name(str(values["name"]))
    if not values["name"]:
        raise ValueError("provider profile name cannot be empty")
    values.update(_validated_profile_updates(values))
    _validate_profile_capability_models(values)
    return values


def _validated_profile_updates(updates: dict[str, Any]) -> dict[str, Any]:
    values = dict(updates)
    allowed = {
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
        "enabled",
        "api_dialect",
        "auth_mode",
        "local_chat_options",
        "local_embedding_options",
    }
    if values.keys() - allowed:
        raise ValueError("unsupported provider profile fields")
    if "provider_kind" in values and values["provider_kind"] not in {
        "fake",
        "openai_compatible",
        "local",
    }:
        raise ValueError("provider kind is invalid")
    for field_name in ("base_url", "chat_model", "embedding_model"):
        if field_name in values:
            values[field_name] = str(values[field_name]).strip()
    if "api_dialect" in values and values["api_dialect"] not in {
        "auto",
        "openai",
        "deepseek",
        "generic",
    }:
        raise ValueError("provider API dialect is invalid")
    if "auth_mode" in values and values["auth_mode"] not in {"api_key", "none"}:
        raise ValueError("provider auth mode is invalid")
    for key in ("local_chat_options", "local_embedding_options"):
        if key in values:
            values[key] = _local_options(values[key])
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


def _validate_profile_capability_models(values: dict[str, Any]) -> None:
    for capability, model in (
        ("chat_capable", "chat_model"),
        ("embedding_capable", "embedding_model"),
    ):
        if values.get(capability) and not values.get(model):
            raise ValueError(f"{model} cannot be empty when its capability is enabled")


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


async def _clear_active_credential(
    session: AsyncSession, profile_id: int, credential_id: int
) -> None:
    states = ProviderPoolStateRepository(session)
    state = await states.get(profile_id)
    if state is None or state.active_credential_id != credential_id:
        return
    await states.compare_and_swap(
        profile_id, expected_version=state.version, active_credential_id=None
    )
