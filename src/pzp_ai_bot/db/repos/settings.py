from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from pzp_ai_bot.db.models import (
    RuntimeSetting,
    ScopeMetadata,
    ScopeSettings,
    SummaryTemplate,
    SystemPromptTemplate,
)
from pzp_ai_bot.memory.scope import Scope

from .shared import (
    EffectiveScopeSettings,
    _nullable_eq,
    _optional_int,
    _required_int,
    _required_str,
    _setting_payload_value,
    _settings_resolution_order,
    normalize_profile_name,
    utc_now,
)


class ScopeMetadataRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        scope: Scope,
        *,
        chat_title: str | None = None,
        topic_title: str | None = None,
        chat_username: str | None = None,
    ) -> ScopeMetadata | None:
        if scope.chat_id is None:
            return None
        rows = await self._rows(scope)
        if not rows:
            row = ScopeMetadata(
                chat_id=scope.chat_id,
                message_thread_id=scope.thread_id,
                chat_title=chat_title,
                topic_title=topic_title,
                chat_username=chat_username,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row = rows[0]
            if chat_title:
                row.chat_title = chat_title
            if topic_title:
                row.topic_title = topic_title
            if chat_username:
                row.chat_username = chat_username
            row.updated_at = utc_now()
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def get(self, scope: Scope) -> ScopeMetadata | None:
        rows = await self._rows(scope)
        return rows[0] if rows else None

    async def get_effective(self, scope: Scope) -> ScopeMetadata | None:
        exact = await self.get(scope)
        if exact is not None or scope.chat_id is None or scope.thread_id is None:
            return exact
        return await self.get(Scope(scope.chat_id, None))

    async def list_all(self) -> dict[str, ScopeMetadata]:
        rows = list((await self.session.scalars(select(ScopeMetadata))).all())
        return {Scope(row.chat_id, row.message_thread_id).key: row for row in rows}

    async def _rows(self, scope: Scope) -> list[ScopeMetadata]:
        if scope.chat_id is None:
            return []
        stmt = select(ScopeMetadata).where(
            ScopeMetadata.chat_id == scope.chat_id,
            _nullable_eq(ScopeMetadata.message_thread_id, scope.thread_id),
        )
        return list((await self.session.scalars(stmt.order_by(ScopeMetadata.id.asc()))).all())


class ScopeSettingsRepository:
    SETTING_FIELDS: ClassVar[set[str]] = {
        "retrieval_top_k",
        "summary_batch_size",
        "summary_model",
        "system_prompt_template",
        "provider_profile",
        "chat_provider_profile",
        "summary_provider_profile",
        "embedding_provider_profile",
        "chat_model",
        "embedding_model",
        "reasoning_level",
        "raw_message_retention_days",
        "knowledge_retention_days",
        "review_before_compact",
    }

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_settings(
        self, scope: Scope, updates: dict[str, int | str | bool | None], updated_by: int | None
    ) -> ScopeSettings:
        unknown = set(updates) - self.SETTING_FIELDS
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        rows = await self._rows(scope)
        if not rows:
            row = ScopeSettings(
                chat_id=scope.chat_id,
                message_thread_id=scope.thread_id,
                updated_by=updated_by,
                updated_at=utc_now(),
                version=1,
            )
            self.session.add(row)
        else:
            row = rows[0]
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
            row.version += 1
        for key, value in updates.items():
            setattr(row, key, value)
        row.updated_by = updated_by
        row.updated_at = utc_now()
        await self.session.flush()
        return row

    async def set_settings_versioned(
        self,
        scope: Scope,
        updates: dict[str, int | str | bool | None],
        updated_by: int | None,
        *,
        expected_version: int,
    ) -> ScopeSettings | None:
        unknown = set(updates) - self.SETTING_FIELDS
        if unknown:
            raise ValueError(f"unknown setting: {sorted(unknown)[0]}")
        rows = await self._rows(scope)
        if not rows:
            if expected_version != 0:
                return None
            row = ScopeSettings(
                chat_id=scope.chat_id,
                message_thread_id=scope.thread_id,
                updated_by=updated_by,
                updated_at=utc_now(),
                version=1,
            )
            for key, value in updates.items():
                setattr(row, key, value)
            self.session.add(row)
            await self.session.flush()
            return row
        row = rows[0]
        if row.version != expected_version:
            return None
        for duplicate in rows[1:]:
            await self.session.delete(duplicate)
        values: dict[str, Any] = {
            **updates,
            "updated_by": updated_by,
            "updated_at": utc_now(),
            "version": expected_version + 1,
        }
        result = await self.session.execute(
            update(ScopeSettings)
            .where(ScopeSettings.id == row.id, ScopeSettings.version == expected_version)
            .values(**values)
            .returning(ScopeSettings.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return None
        await self.session.refresh(row)
        return row

    async def current_version(self, scope: Scope) -> int:
        stmt = select(ScopeSettings.version).where(
            _nullable_eq(ScopeSettings.chat_id, scope.chat_id),
            _nullable_eq(ScopeSettings.message_thread_id, scope.thread_id),
        )
        version = await self.session.scalar(stmt.order_by(ScopeSettings.id.asc()))
        return int(version) if version is not None else 0

    async def effective(
        self, scope: Scope, defaults: EffectiveScopeSettings, *, _attempt: int = 0
    ) -> EffectiveScopeSettings:
        from pzp_ai_bot.db.repos.embedding_indexes import (
            EmbeddingIndexRepository,
            EmbeddingRouteChanged,
        )

        indexes = EmbeddingIndexRepository(self.session)
        epoch = await indexes.current_epoch()
        values = defaults.as_dict()
        for candidate in _settings_resolution_order(scope):
            row = await self.get(candidate)
            if row is None:
                continue
            for operation in ("chat", "summary", "embedding"):
                route_key = f"{operation}_provider_profile"
                next_profile = getattr(row, route_key) or (
                    row.provider_profile if values.get(route_key) is None else None
                )
                if next_profile is not None:
                    pair = defaults.provider_models.get(next_profile)
                    if pair is not None:
                        values[f"{operation}_model"] = pair[1 if operation == "embedding" else 0]
            for key in self.SETTING_FIELDS:
                value = getattr(row, key)
                if value is not None:
                    values[key] = value
        if epoch != await indexes.current_epoch():
            if _attempt >= 2:
                raise EmbeddingRouteChanged()
            return await self.effective(scope, defaults, _attempt=_attempt + 1)
        return EffectiveScopeSettings(
            retrieval_top_k=_required_int(values["retrieval_top_k"]),
            summary_batch_size=_required_int(values["summary_batch_size"]),
            summary_model=_required_str(values["summary_model"]),
            system_prompt_template=_required_str(values["system_prompt_template"]),
            provider_profile=_required_str(values["provider_profile"]),
            chat_model=_required_str(values["chat_model"]),
            embedding_model=_required_str(values["embedding_model"]),
            reasoning_level=_required_str(values["reasoning_level"]),
            raw_message_retention_days=_optional_int(values["raw_message_retention_days"]),
            knowledge_retention_days=_optional_int(values["knowledge_retention_days"]),
            review_before_compact=bool(values["review_before_compact"]),
            chat_provider_profile=str(values["chat_provider_profile"])
            if values.get("chat_provider_profile") is not None
            else None,
            summary_provider_profile=str(values["summary_provider_profile"])
            if values.get("summary_provider_profile") is not None
            else None,
            embedding_provider_profile=str(values["embedding_provider_profile"])
            if values.get("embedding_provider_profile") is not None
            else None,
            provider_models=defaults.provider_models,
            routing_epoch=epoch,
        )

    async def get(self, scope: Scope) -> ScopeSettings | None:
        rows = await self._rows(scope)
        return rows[0] if rows else None

    async def _rows(self, scope: Scope) -> list[ScopeSettings]:
        stmt = select(ScopeSettings).where(
            _nullable_eq(ScopeSettings.chat_id, scope.chat_id),
            _nullable_eq(ScopeSettings.message_thread_id, scope.thread_id),
        )
        return list((await self.session.scalars(stmt.order_by(ScopeSettings.id.asc()))).all())


class SummaryTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, *, name: str, prompt: str, updated_by: int | None = None
    ) -> SummaryTemplate:
        clean_name = normalize_profile_name(name)
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("summary template prompt cannot be empty")
        rows = list(
            (
                await self.session.scalars(
                    select(SummaryTemplate)
                    .where(SummaryTemplate.name == clean_name)
                    .order_by(SummaryTemplate.id.asc())
                )
            ).all()
        )
        now = utc_now()
        if not rows:
            row = SummaryTemplate(
                name=clean_name,
                prompt=clean_prompt,
                created_by=updated_by,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row = rows[0]
            row.prompt = clean_prompt
            row.updated_at = now
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def get_prompt(self, name: str) -> str | None:
        clean_name = normalize_profile_name(name)
        row = await self.session.scalar(
            select(SummaryTemplate).where(SummaryTemplate.name == clean_name)
        )
        return row.prompt if row is not None else None

    async def list_templates(self) -> list[SummaryTemplate]:
        return list(
            (
                await self.session.scalars(
                    select(SummaryTemplate).order_by(SummaryTemplate.name.asc())
                )
            ).all()
        )


class SystemPromptTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, *, name: str, prompt: str, version: str, updated_by: int | None = None
    ) -> SystemPromptTemplate:
        clean_name = normalize_profile_name(name)
        clean_prompt = prompt.strip()
        clean_version = version.strip()
        if not clean_prompt:
            raise ValueError("system prompt template cannot be empty")
        if not clean_version:
            raise ValueError("system prompt version cannot be empty")
        rows = list(
            (
                await self.session.scalars(
                    select(SystemPromptTemplate)
                    .where(SystemPromptTemplate.name == clean_name)
                    .order_by(SystemPromptTemplate.id.asc())
                )
            ).all()
        )
        now = utc_now()
        if not rows:
            row = SystemPromptTemplate(
                name=clean_name,
                prompt=clean_prompt,
                version=clean_version,
                created_by=updated_by,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
        else:
            row = rows[0]
            row.prompt = clean_prompt
            row.version = clean_version
            row.updated_at = now
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        await self.session.flush()
        return row

    async def get(self, name: str) -> SystemPromptTemplate | None:
        clean_name = normalize_profile_name(name)
        return await self.session.scalar(
            select(SystemPromptTemplate).where(SystemPromptTemplate.name == clean_name)
        )

    async def list_templates(self) -> list[SystemPromptTemplate]:
        return list(
            (
                await self.session.scalars(
                    select(SystemPromptTemplate).order_by(SystemPromptTemplate.name.asc())
                )
            ).all()
        )


@dataclass(frozen=True, slots=True)
class RuntimeSettingView:
    key: str
    value: Any
    previous_value: Any
    updated_by: int | None
    updated_at: datetime
    version: int


class RuntimeSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_settings(self) -> list[RuntimeSettingView]:
        rows = list(
            (
                await self.session.scalars(
                    select(RuntimeSetting).order_by(RuntimeSetting.key.asc())
                )
            ).all()
        )
        return [runtime_setting_view(row) for row in rows]

    async def values(self) -> dict[str, Any]:
        rows = list((await self.session.scalars(select(RuntimeSetting))).all())
        return {row.key: _setting_payload_value(row.value) for row in rows}

    async def get(self, key: str) -> RuntimeSettingView | None:
        row = await self.session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.key == key.strip().lower())
        )
        return runtime_setting_view(row) if row is not None else None

    async def current_version(self, key: str) -> int:
        version = await self.session.scalar(
            select(RuntimeSetting.version).where(RuntimeSetting.key == key.strip().lower())
        )
        return int(version) if version is not None else 0

    async def set_value(
        self, *, key: str, value: Any, updated_by: int | None = None
    ) -> RuntimeSettingView:
        clean_key = key.strip().lower()
        row = await self.session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.key == clean_key)
        )
        payload = {"value": value}
        now = utc_now()
        if row is None:
            row = RuntimeSetting(
                key=clean_key,
                value=payload,
                previous_value=None,
                updated_by=updated_by,
                updated_at=now,
                version=1,
            )
            self.session.add(row)
        else:
            row.previous_value = dict(row.value)
            row.value = payload
            row.updated_by = updated_by
            row.updated_at = now
            row.version += 1
        await self.session.flush()
        return runtime_setting_view(row)

    async def set_value_versioned(
        self, *, key: str, value: Any, expected_version: int, updated_by: int | None = None
    ) -> RuntimeSettingView | None:
        clean_key = key.strip().lower()
        row = await self.session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.key == clean_key)
        )
        payload = {"value": value}
        now = utc_now()
        if row is None:
            if expected_version != 0:
                return None
            row = RuntimeSetting(
                key=clean_key,
                value=payload,
                previous_value=None,
                updated_by=updated_by,
                updated_at=now,
                version=1,
            )
            self.session.add(row)
        else:
            if row.version != expected_version:
                return None
            result = await self.session.execute(
                update(RuntimeSetting)
                .where(RuntimeSetting.id == row.id, RuntimeSetting.version == expected_version)
                .values(
                    previous_value=dict(row.value),
                    value=payload,
                    updated_by=updated_by,
                    updated_at=now,
                    version=expected_version + 1,
                )
                .returning(RuntimeSetting.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                return None
            await self.session.refresh(row)
            return runtime_setting_view(row)
        await self.session.flush()
        return runtime_setting_view(row)


def runtime_setting_view(row: RuntimeSetting) -> RuntimeSettingView:
    return RuntimeSettingView(
        key=row.key,
        value=_setting_payload_value(row.value),
        previous_value=_setting_payload_value(row.previous_value),
        updated_by=row.updated_by,
        updated_at=row.updated_at,
        version=row.version,
    )
