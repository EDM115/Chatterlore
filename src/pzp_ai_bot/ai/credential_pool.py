from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.secrets.crypto import EncryptedProviderSecret, ProviderSecretVault
from pzp_ai_bot.db.models import AIProviderCredential, AIProviderProfile
from pzp_ai_bot.db.repos.alerts import AdminAlertRepository
from pzp_ai_bot.db.repos.provider_credentials import (
    ProviderCredentialRepository,
    ProviderPoolStateRepository,
    ProviderProfileRepository,
)
from pzp_ai_bot.db.repos.shared import normalize_profile_name
from pzp_ai_bot.db.session import session_scope

from .failure_classification import ProviderFailureDecision
from .provider_call_policy import ProviderCallPolicy, ProviderLeaseCallError

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True, repr=False)
class CredentialLease:
    profile_id: int
    profile_name: str
    profile_revision: int
    credential_id: int
    credential_revision: int
    label: str
    fingerprint: str
    source: str
    _secret: str

    def secret_value(self) -> str:
        return self._secret

    def __repr__(self) -> str:
        return (
            "CredentialLease("
            f"profile_name={self.profile_name!r}, profile_revision={self.profile_revision}, "
            f"label={self.label!r}, fingerprint={self.fingerprint!r}, source={self.source!r})"
        )


@dataclass(frozen=True, slots=True)
class ProviderPoolAttemptMetadata:
    profile_revision: int
    credential_fingerprint: str | None
    credential_label: str | None
    attempted_credentials: int
    same_key_attempts: int
    rotation_count: int
    final_classification: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "managed_profile_revision": self.profile_revision,
            "credential_fingerprint": self.credential_fingerprint,
            "credential_label": self.credential_label,
            "attempted_credential_count": self.attempted_credentials,
            "same_key_attempt_count": self.same_key_attempts,
            "rotation_count": self.rotation_count,
            "final_classification": self.final_classification,
        }


@dataclass(frozen=True, slots=True)
class ProviderPoolCallResult[T]:
    value: T
    metadata: ProviderPoolAttemptMetadata


@dataclass(frozen=True, slots=True)
class ProviderPoolSummary:
    profile_name: str
    profile_revision: int
    active_credential_fingerprint: str | None
    active_credential_label: str | None
    health_epoch: int
    total_credentials: int
    eligible_credentials: int
    cooldown_credentials: int
    disabled_credentials: int
    last_exhausted_at: datetime | None
    last_recovered_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_name": self.profile_name,
            "profile_revision": self.profile_revision,
            "active_credential_fingerprint": self.active_credential_fingerprint,
            "active_credential_label": self.active_credential_label,
            "health_epoch": self.health_epoch,
            "total_credentials": self.total_credentials,
            "eligible_credentials": self.eligible_credentials,
            "cooldown_credentials": self.cooldown_credentials,
            "disabled_credentials": self.disabled_credentials,
            "last_exhausted_at": self.last_exhausted_at,
            "last_recovered_at": self.last_recovered_at,
        }


class ProviderCallError(RuntimeError):
    def __init__(
        self,
        *,
        classification: str,
        metadata: ProviderPoolAttemptMetadata,
        original_error: Exception,
    ) -> None:
        super().__init__(f"provider call failed ({classification})")
        self.classification = classification
        self.metadata = metadata
        self.original_error = original_error


class ProviderPoolExhaustedError(RuntimeError):
    def __init__(
        self,
        profile_name: str,
        attempted_count: int,
        earliest_retry_at: datetime | None,
        metadata: ProviderPoolAttemptMetadata,
    ) -> None:
        super().__init__(f"provider profile {profile_name!r} is temporarily unavailable")
        self.profile_name = profile_name
        self.attempted_count = attempted_count
        self.earliest_retry_at = earliest_retry_at
        self.metadata = metadata


@dataclass(frozen=True, slots=True)
class _Selection:
    lease: CredentialLease | None
    profile_revision: int
    earliest_retry_at: datetime | None


class CredentialPoolService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        vault: ProviderSecretVault,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.vault = vault
        self.clock = clock or (lambda: datetime.now(UTC))
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.call_policy = ProviderCallPolicy(
            attempts=settings.provider_retry_attempts,
            backoff_seconds=settings.provider_retry_backoff_seconds,
            clock=self.clock,
        )

    async def execute[T](
        self, profile_name: str, operation: Callable[[CredentialLease], Awaitable[T]]
    ) -> ProviderPoolCallResult[T]:
        clean = normalize_profile_name(profile_name)
        attempted: set[int] = set()
        cursor_id: int | None = None
        rotations = 0
        same_key_attempts = 0
        final_fingerprint: str | None = None
        final_label: str | None = None
        profile_revision = 0
        earliest_retry_at: datetime | None = None
        while len(attempted) < min(10, self.settings.control_plane_max_credentials_per_profile):
            selection = await self._select(clean, attempted=attempted, cursor_id=cursor_id)
            profile_revision = selection.profile_revision
            earliest_retry_at = selection.earliest_retry_at
            lease = selection.lease
            if lease is None:
                break
            attempted.add(lease.credential_id)
            cursor_id = lease.credential_id
            final_fingerprint = lease.fingerprint
            final_label = lease.label
            try:
                lease_result = await self.call_policy.execute(lease, operation)
            except ProviderLeaseCallError as failed:
                same_key_attempts += failed.metadata.same_key_attempts
                classification = failed.decision.classification.value
                metadata = _metadata(
                    profile_revision,
                    final_fingerprint,
                    final_label,
                    len(attempted),
                    same_key_attempts,
                    rotations,
                    classification,
                )
                if not failed.decision.rotate:
                    raise ProviderCallError(
                        classification=classification,
                        metadata=metadata,
                        original_error=failed.original_error,
                    ) from failed.original_error
                await self._apply_failure(lease, failed.decision)
                rotations += 1
                continue
            same_key_attempts += lease_result.metadata.same_key_attempts
            await self._apply_success(lease)
            return ProviderPoolCallResult(
                value=lease_result.value,
                metadata=_metadata(
                    profile_revision,
                    final_fingerprint,
                    final_label,
                    len(attempted),
                    same_key_attempts,
                    rotations,
                    "success",
                ),
            )
        await self._mark_exhausted(clean)
        metadata = _metadata(
            profile_revision,
            final_fingerprint,
            final_label,
            len(attempted),
            same_key_attempts,
            rotations,
            "exhausted",
        )
        raise ProviderPoolExhaustedError(clean, len(attempted), earliest_retry_at, metadata)

    async def execute_credential[T](
        self,
        profile_name: str,
        credential_id: int,
        operation: Callable[[CredentialLease], Awaitable[T]],
    ) -> ProviderPoolCallResult[T]:
        clean = normalize_profile_name(profile_name)
        lease = await self._specific_lease(clean, credential_id)
        try:
            lease_result = await self.call_policy.execute(lease, operation)
        except ProviderLeaseCallError as failed:
            classification = failed.decision.classification.value
            if failed.decision.state_transition is not None:
                await self._apply_failure(lease, failed.decision)
            metadata = _metadata(
                lease.profile_revision,
                lease.fingerprint,
                lease.label,
                1,
                failed.metadata.same_key_attempts,
                0,
                classification,
            )
            raise ProviderCallError(
                classification=classification,
                metadata=metadata,
                original_error=failed.original_error,
            ) from failed.original_error
        await self._apply_success(lease)
        return ProviderPoolCallResult(
            value=lease_result.value,
            metadata=_metadata(
                lease.profile_revision,
                lease.fingerprint,
                lease.label,
                1,
                lease_result.metadata.same_key_attempts,
                0,
                "success",
            ),
        )

    async def summary(self, profile_name: str) -> ProviderPoolSummary:
        clean = normalize_profile_name(profile_name)
        async with self._locks[clean], session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            profile = await profiles.get_by_name(clean)
            if profile is None:
                raise ValueError("managed provider profile was not found")
            state = await profiles.get_pool_state(profile.id)
            credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
            now = self.clock()
            active = next(
                (
                    item
                    for item in credentials
                    if state is not None
                    and item.id == state.active_credential_id
                    and item.lifecycle_state == "enabled"
                    and item.health_state != "disabled"
                    and (item.cooldown_until is None or _as_aware(item.cooldown_until) <= now)
                ),
                None,
            )
            enabled = [item for item in credentials if item.lifecycle_state == "enabled"]
            cooldown = [
                item
                for item in enabled
                if item.cooldown_until is not None and _as_aware(item.cooldown_until) > now
            ]
            disabled = [item for item in enabled if item.health_state == "disabled"]
            eligible = [
                item
                for item in enabled
                if item.health_state != "disabled"
                and (item.cooldown_until is None or _as_aware(item.cooldown_until) <= now)
            ]
            return ProviderPoolSummary(
                profile_name=profile.name,
                profile_revision=profile.version,
                active_credential_fingerprint=active.fingerprint if active else None,
                active_credential_label=active.label if active else None,
                health_epoch=state.health_epoch if state else 0,
                total_credentials=len(credentials),
                eligible_credentials=len(eligible),
                cooldown_credentials=len(cooldown),
                disabled_credentials=len(disabled),
                last_exhausted_at=state.last_exhausted_at if state else None,
                last_recovered_at=state.last_recovered_at if state else None,
            )

    async def _specific_lease(self, profile_name: str, credential_id: int) -> CredentialLease:
        async with self._locks[profile_name], session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(profile_name)
            credential = await ProviderCredentialRepository(session).get(credential_id)
            if (
                profile is None
                or credential is None
                or credential.profile_id != profile.id
                or credential.lifecycle_state != "enabled"
            ):
                raise ValueError("provider credential was not found")
            return self._lease(profile, credential)

    async def _select(
        self, profile_name: str, *, attempted: set[int], cursor_id: int | None
    ) -> _Selection:
        async with self._locks[profile_name], session_scope(self.session_factory) as session:
            profiles = ProviderProfileRepository(session)
            profile = await profiles.get_by_name(profile_name)
            if profile is None or not profile.enabled:
                raise ValueError("managed provider profile is unavailable")
            pool_state = await profiles.get_pool_state(profile.id)
            credentials = await ProviderCredentialRepository(session).list_for_profile(profile.id)
            now = self.clock()
            eligible: list[AIProviderCredential] = []
            cooldowns: list[datetime] = []
            for credential in credentials[:10]:
                if credential.lifecycle_state != "enabled" or credential.health_state == "disabled":
                    continue
                if (
                    credential.cooldown_until is not None
                    and _as_aware(credential.cooldown_until) > now
                ):
                    cooldowns.append(_as_aware(credential.cooldown_until))
                    continue
                if credential.id not in attempted:
                    eligible.append(credential)
            if not eligible:
                return _Selection(
                    lease=None,
                    profile_revision=profile.version,
                    earliest_retry_at=min(cooldowns) if cooldowns else None,
                )
            active_id = pool_state.active_credential_id if pool_state is not None else None
            selected = _circular_choice(
                credentials,
                eligible,
                preferred_id=active_id if cursor_id is None else None,
                cursor_id=cursor_id,
            )
            return _Selection(
                lease=self._lease(profile, selected),
                profile_revision=profile.version,
                earliest_retry_at=min(cooldowns) if cooldowns else None,
            )

    def _lease(
        self, profile: AIProviderProfile, credential: AIProviderCredential
    ) -> CredentialLease:
        fingerprint = credential.fingerprint
        if fingerprint is None:
            raise ValueError("provider credential fingerprint is unavailable")
        if credential.source_kind == "environment":
            bootstrap = self.settings.provider_profiles.get(profile.name)
            if bootstrap is None or not bootstrap.openai_api_key:
                raise ValueError("environment provider credential is unavailable")
            key_id = credential.master_key_id or self.settings.control_plane_active_master_key_id
            if self.vault.fingerprint(bootstrap.openai_api_key, key_id=key_id) != fingerprint:
                raise ValueError("environment provider credential metadata is stale")
            secret = bootstrap.openai_api_key
        else:
            secret = self.vault.decrypt(
                _encrypted(credential), profile_name=profile.name, credential_id=credential.id
            )
        return CredentialLease(
            profile_id=profile.id,
            profile_name=profile.name,
            profile_revision=profile.version,
            credential_id=credential.id,
            credential_revision=credential.version,
            label=credential.label,
            fingerprint=fingerprint,
            source=credential.source_kind,
            _secret=secret,
        )

    async def _apply_failure(
        self, lease: CredentialLease, decision: ProviderFailureDecision
    ) -> bool:
        if decision.state_transition is None:
            return False
        async with self._locks[lease.profile_name], session_scope(self.session_factory) as session:
            credentials = ProviderCredentialRepository(session)
            stored = await credentials.get(lease.credential_id)
            if stored is None or stored.version != lease.credential_revision:
                return False
            cooldown_until = (
                self.clock() + timedelta(seconds=decision.retry_after_seconds or 0)
                if decision.state_transition == "cooldown"
                else None
            )
            changed = await credentials.apply_failure(
                lease.credential_id,
                expected_version=lease.credential_revision,
                health_state=decision.state_transition,
                classification=decision.classification.value,
                cooldown_until=cooldown_until,
                now=self.clock(),
            )
            if changed is None:
                return False
            states = ProviderPoolStateRepository(session)
            state = await states.get(lease.profile_id)
            health_epoch = 1
            if state is not None:
                updated_state = await states.compare_and_swap(
                    lease.profile_id,
                    expected_version=state.version,
                    active_credential_id=None,
                    health_epoch_delta=1,
                )
                if updated_state is not None:
                    health_epoch = updated_state.health_epoch
            if decision.alert_type is not None:
                await AdminAlertRepository(session).enqueue_intent(
                    alert_type=decision.alert_type,
                    profile_name=lease.profile_name,
                    credential_fingerprint=lease.fingerprint,
                    credential_label=lease.label,
                    health_epoch=health_epoch,
                    payload={"classification": decision.classification.value},
                    now=self.clock(),
                )
            return True

    async def _apply_success(self, lease: CredentialLease) -> None:
        async with self._locks[lease.profile_name], session_scope(self.session_factory) as session:
            credentials = ProviderCredentialRepository(session)
            stored = await credentials.get(lease.credential_id)
            if stored is None or stored.lifecycle_state != "enabled":
                return
            recovered = stored.health_state in {"cooldown", "disabled"}
            changed = await credentials.apply_success(
                lease.credential_id, expected_version=stored.version, now=self.clock()
            )
            if changed is None:
                return
            states = ProviderPoolStateRepository(session)
            state = await states.get(lease.profile_id)
            if state is not None:
                recovered = recovered or (
                    state.last_exhausted_at is not None
                    and (
                        state.last_recovered_at is None
                        or state.last_recovered_at < state.last_exhausted_at
                    )
                )
                updated_state = await states.compare_and_swap(
                    lease.profile_id,
                    expected_version=state.version,
                    active_credential_id=lease.credential_id,
                    health_epoch_delta=1 if recovered else 0,
                    recovered_at=self.clock() if recovered else None,
                )
                if recovered and updated_state is not None:
                    await AdminAlertRepository(session).enqueue_intent(
                        alert_type="credential_recovered",
                        profile_name=lease.profile_name,
                        credential_fingerprint=lease.fingerprint,
                        credential_label=lease.label,
                        health_epoch=updated_state.health_epoch,
                        payload={"classification": "recovered"},
                        now=self.clock(),
                    )

    async def _mark_exhausted(self, profile_name: str) -> None:
        async with self._locks[profile_name], session_scope(self.session_factory) as session:
            profile = await ProviderProfileRepository(session).get_by_name(profile_name)
            if profile is None:
                return
            states = ProviderPoolStateRepository(session)
            state = await states.get(profile.id)
            if state is not None:
                updated_state = await states.compare_and_swap(
                    profile.id,
                    expected_version=state.version,
                    active_credential_id=None,
                    exhausted_at=self.clock(),
                )
                if updated_state is not None:
                    await AdminAlertRepository(session).enqueue_intent(
                        alert_type="pool_exhausted",
                        profile_name=profile.name,
                        credential_fingerprint=None,
                        credential_label=None,
                        health_epoch=updated_state.health_epoch,
                        payload={"classification": "exhausted"},
                        now=self.clock(),
                    )


def _circular_choice(
    ordered: list[AIProviderCredential],
    eligible: list[AIProviderCredential],
    *,
    preferred_id: int | None,
    cursor_id: int | None,
) -> AIProviderCredential:
    eligible_by_id = {item.id: item for item in eligible}
    if preferred_id in eligible_by_id:
        return eligible_by_id[preferred_id]
    ordered_ids = [item.id for item in ordered]
    start = 0
    if cursor_id in ordered_ids:
        start = (ordered_ids.index(cursor_id) + 1) % len(ordered_ids)
    for offset in range(len(ordered_ids)):
        candidate_id = ordered_ids[(start + offset) % len(ordered_ids)]
        if candidate_id in eligible_by_id:
            return eligible_by_id[candidate_id]
    raise RuntimeError("eligible credential selection reached an invalid state")


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
        raise ValueError("provider credential cannot be decrypted")
    return EncryptedProviderSecret(
        ciphertext=ciphertext,
        secret_nonce=secret_nonce,
        wrapped_dek=wrapped_dek,
        wrap_nonce=wrap_nonce,
        master_key_id=master_key_id,
        fingerprint=fingerprint,
        last_four=last_four,
    )


def _metadata(
    profile_revision: int,
    fingerprint: str | None,
    label: str | None,
    attempted_credentials: int,
    same_key_attempts: int,
    rotations: int,
    classification: str,
) -> ProviderPoolAttemptMetadata:
    return ProviderPoolAttemptMetadata(
        profile_revision=profile_revision,
        credential_fingerprint=fingerprint,
        credential_label=label,
        attempted_credentials=attempted_credentials,
        same_key_attempts=same_key_attempts,
        rotation_count=rotations,
        final_classification=classification,
    )


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "CredentialLease",
    "CredentialPoolService",
    "ProviderCallError",
    "ProviderPoolAttemptMetadata",
    "ProviderPoolCallResult",
    "ProviderPoolExhaustedError",
    "ProviderPoolSummary",
]
