from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import select
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
            )
            self.session.add(row)
        else:
            row = rows[0]
            for duplicate in rows[1:]:
                await self.session.delete(duplicate)
        for key, value in updates.items():
            setattr(row, key, value)
        row.updated_by = updated_by
        row.updated_at = utc_now()
        await self.session.flush()
        return row

    async def effective(
        self, scope: Scope, defaults: EffectiveScopeSettings
    ) -> EffectiveScopeSettings:
        values = defaults.as_dict()
        for candidate in _settings_resolution_order(scope):
            row = await self.get(candidate)
            if row is None:
                continue
            for key in self.SETTING_FIELDS:
                value = getattr(row, key)
                if value is not None:
                    values[key] = value
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


def runtime_setting_view(row: RuntimeSetting) -> RuntimeSettingView:
    return RuntimeSettingView(
        key=row.key,
        value=_setting_payload_value(row.value),
        previous_value=_setting_payload_value(row.previous_value),
        updated_by=row.updated_by,
        updated_at=row.updated_at,
        version=row.version,
    )
