from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import AIProviderCredential, AIProviderPoolState, AIProviderProfile
from pzp_ai_bot.db.repos.shared import normalize_profile_name

_PROFILE_UPDATE_FIELDS = {
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
}
_CREDENTIAL_UPDATE_FIELDS = {"label", "position", "lifecycle_state"}


class ProviderProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[AIProviderProfile]:
        statement = select(AIProviderProfile).order_by(AIProviderProfile.name)
        return list((await self.session.scalars(statement)).all())

    async def count(self) -> int:
        return int(
            await self.session.scalar(select(func.count()).select_from(AIProviderProfile)) or 0
        )

    async def get_by_id(self, profile_id: int) -> AIProviderProfile | None:
        return await self.session.get(AIProviderProfile, profile_id)

    async def get_by_name(self, name: str) -> AIProviderProfile | None:
        return await self.session.scalar(
            select(AIProviderProfile).where(AIProviderProfile.name == normalize_profile_name(name))
        )

    async def get_pool_state(self, profile_id: int) -> AIProviderPoolState | None:
        return await self.session.scalar(
            select(AIProviderPoolState).where(AIProviderPoolState.profile_id == profile_id)
        )

    async def create(
        self,
        *,
        name: str,
        provider_kind: str,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        chat_capable: bool,
        embedding_capable: bool,
        reasoning_capable: bool,
        local: bool,
        redact_before_ai: bool,
        redaction_patterns: list[str],
        fake_embedding_dimensions: int,
        actor_user_id: int,
        now: datetime,
        enabled: bool = True,
    ) -> AIProviderProfile:
        profile = AIProviderProfile(
            name=normalize_profile_name(name),
            provider_kind=provider_kind,
            base_url=base_url,
            chat_model=chat_model,
            embedding_model=embedding_model,
            chat_capable=chat_capable,
            embedding_capable=embedding_capable,
            reasoning_capable=reasoning_capable,
            local=local,
            redact_before_ai=redact_before_ai,
            redaction_patterns=list(redaction_patterns),
            fake_embedding_dimensions=fake_embedding_dimensions,
            enabled=enabled,
            source_kind="managed",
            created_by=actor_user_id,
            created_at=now,
            updated_by=actor_user_id,
            updated_at=now,
            version=1,
        )
        self.session.add(profile)
        await self.session.flush()
        self.session.add(
            AIProviderPoolState(
                profile_id=profile.id,
                active_credential_id=None,
                health_epoch=0,
                last_exhausted_at=None,
                last_recovered_at=None,
                version=1,
            )
        )
        await self.session.flush()
        return profile

    async def update(
        self,
        profile_id: int,
        *,
        expected_version: int,
        updates: dict[str, Any],
        actor_user_id: int,
        now: datetime,
    ) -> AIProviderProfile | None:
        values = {key: value for key, value in updates.items() if key in _PROFILE_UPDATE_FIELDS}
        if not values:
            return await self.get_by_id(profile_id)
        values.update(
            updated_by=actor_user_id, updated_at=now, version=AIProviderProfile.version + 1
        )
        statement = (
            update(AIProviderProfile)
            .where(
                AIProviderProfile.id == profile_id, AIProviderProfile.version == expected_version
            )
            .values(**values)
            .returning(AIProviderProfile)
        )
        return (await self.session.scalars(statement)).one_or_none()

    async def delete(self, profile_id: int, *, expected_version: int) -> AIProviderProfile | None:
        profile = await self.get_by_id(profile_id)
        if profile is None or profile.version != expected_version:
            return None
        await self.session.delete(profile)
        await self.session.flush()
        return profile


class ProviderCredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_profile(
        self, profile_id: int, *, include_deleted: bool = False
    ) -> list[AIProviderCredential]:
        statement = select(AIProviderCredential).where(
            AIProviderCredential.profile_id == profile_id
        )
        if not include_deleted:
            statement = statement.where(AIProviderCredential.lifecycle_state != "deleted")
        statement = statement.order_by(AIProviderCredential.position, AIProviderCredential.id)
        return list((await self.session.scalars(statement)).all())

    async def list_encrypted(self) -> list[AIProviderCredential]:
        statement = (
            select(AIProviderCredential)
            .where(
                AIProviderCredential.lifecycle_state != "deleted",
                AIProviderCredential.secret_ciphertext.is_not(None),
            )
            .order_by(AIProviderCredential.id)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_for_profile(self, profile_id: int) -> int:
        statement = (
            select(func.count())
            .select_from(AIProviderCredential)
            .where(
                AIProviderCredential.profile_id == profile_id,
                AIProviderCredential.lifecycle_state != "deleted",
            )
        )
        return int(await self.session.scalar(statement) or 0)

    async def get(self, credential_id: int) -> AIProviderCredential | None:
        return await self.session.get(AIProviderCredential, credential_id)

    async def create_provisioning(
        self, *, profile_id: int, label: str, position: int, actor_user_id: int, now: datetime
    ) -> AIProviderCredential:
        stored = AIProviderCredential(
            profile_id=profile_id,
            label=label,
            source_kind="managed",
            environment_variable=None,
            secret_ciphertext=None,
            secret_nonce=None,
            wrapped_dek=None,
            wrap_nonce=None,
            master_key_id=None,
            fingerprint=None,
            last_four=None,
            position=position,
            lifecycle_state="provisioning",
            health_state="unknown",
            cooldown_until=None,
            last_error_classification=None,
            success_count=0,
            failure_count=0,
            last_success_at=None,
            last_failure_at=None,
            created_by=actor_user_id,
            created_at=now,
            updated_by=actor_user_id,
            updated_at=now,
            deleted_by=None,
            deleted_at=None,
            version=1,
        )
        self.session.add(stored)
        await self.session.flush()
        return stored

    async def store_encrypted(
        self,
        credential_id: int,
        *,
        ciphertext: bytes,
        secret_nonce: bytes,
        wrapped_dek: bytes,
        wrap_nonce: bytes,
        master_key_id: str,
        fingerprint: str,
        last_four: str,
    ) -> AIProviderCredential:
        stored = await self.get(credential_id)
        if stored is None or stored.lifecycle_state != "provisioning":
            raise ValueError("credential cannot be provisioned")
        stored.secret_ciphertext = ciphertext
        stored.secret_nonce = secret_nonce
        stored.wrapped_dek = wrapped_dek
        stored.wrap_nonce = wrap_nonce
        stored.master_key_id = master_key_id
        stored.fingerprint = fingerprint
        stored.last_four = last_four
        stored.lifecycle_state = "enabled"
        await self.session.flush()
        return stored

    async def update(
        self,
        credential_id: int,
        *,
        expected_version: int,
        updates: dict[str, Any],
        actor_user_id: int,
        now: datetime,
    ) -> AIProviderCredential | None:
        values = {key: value for key, value in updates.items() if key in _CREDENTIAL_UPDATE_FIELDS}
        if not values:
            return await self.get(credential_id)
        values.update(
            updated_by=actor_user_id, updated_at=now, version=AIProviderCredential.version + 1
        )
        statement = (
            update(AIProviderCredential)
            .where(
                AIProviderCredential.id == credential_id,
                AIProviderCredential.version == expected_version,
                AIProviderCredential.lifecycle_state != "deleted",
            )
            .values(**values)
            .returning(AIProviderCredential)
        )
        return (await self.session.scalars(statement)).one_or_none()

    async def tombstone(
        self, credential_id: int, *, expected_version: int, actor_user_id: int, now: datetime
    ) -> AIProviderCredential | None:
        statement = (
            update(AIProviderCredential)
            .where(
                AIProviderCredential.id == credential_id,
                AIProviderCredential.version == expected_version,
                AIProviderCredential.lifecycle_state != "deleted",
            )
            .values(
                source_kind="managed",
                environment_variable=None,
                secret_ciphertext=None,
                secret_nonce=None,
                wrapped_dek=None,
                wrap_nonce=None,
                master_key_id=None,
                fingerprint=None,
                last_four=None,
                lifecycle_state="deleted",
                health_state="unknown",
                cooldown_until=None,
                last_error_classification=None,
                deleted_by=actor_user_id,
                deleted_at=now,
                updated_by=actor_user_id,
                updated_at=now,
                version=AIProviderCredential.version + 1,
            )
            .returning(AIProviderCredential)
        )
        return (await self.session.scalars(statement)).one_or_none()

    async def replace_wrapping(
        self, credential_id: int, *, wrapped_dek: bytes, wrap_nonce: bytes, master_key_id: str
    ) -> None:
        await self.session.execute(
            update(AIProviderCredential)
            .where(AIProviderCredential.id == credential_id)
            .values(wrapped_dek=wrapped_dek, wrap_nonce=wrap_nonce, master_key_id=master_key_id)
        )
