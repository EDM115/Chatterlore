from __future__ import annotations

import hashlib
from datetime import timedelta
from secrets import token_urlsafe
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.repos.challenges import SettingsChallengeRepository
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.profiles import MemoryProfileRepository
from pzp_ai_bot.db.repos.settings import (
    RuntimeSettingRepository,
    RuntimeSettingView,
    ScopeMetadataRepository,
    ScopeSettingsRepository,
    SummaryTemplateRepository,
    SystemPromptTemplateRepository,
)
from pzp_ai_bot.db.repos.shared import EffectiveScopeSettings, MemoryProfileView, utc_now
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.contracts import (
    AIRequestOptions,
    RuntimeSettingPreview,
    SettingsChangePreview,
    SummaryTemplatePreview,
    SystemPromptTemplatePreview,
)
from pzp_ai_bot.memory.prompts import BUILTIN_SYSTEM_PROMPTS, PROMPT_VERSION, PromptTemplate
from pzp_ai_bot.memory.scope import Scope

SAFE_RUNTIME_SETTING_TYPES: dict[str, str] = {
    "learning_default_enabled": "bool",
    "retrieval_candidate_limit": "positive_int",
    "retrieval_top_k": "positive_int",
    "summary_batch_size": "positive_int",
    "summary_idle_seconds": "positive_int",
    "summary_model": "str",
    "system_prompt_template": "str",
    "provider_profile": "str",
    "chat_model": "str",
    "embedding_model": "str",
    "reasoning_level": "str",
    "answer_min_score": "non_negative_float",
    "forget_confirm_ttl_seconds": "positive_int",
    "raw_message_retention_days": "optional_positive_int",
    "knowledge_retention_days": "optional_positive_int",
    "review_before_compact": "bool",
}

BUILTIN_SUMMARY_TEMPLATES: dict[str, str] = {
    "general": "Keep stable facts, decisions, preferences, todos, and open questions.",
    "decisions": "Extract only durable decisions, owners, deadlines, and source-backed rationale.",
    "todos": "Extract action items, owners, due dates when present, blockers, and open follow-ups.",
    "meeting": (
        "Write concise meeting notes with decisions, todos, risks, and unresolved questions."
    ),
    "incident": (
        "Write an incident recap with impact, timeline, decisions, remediation todos, "
        "and open questions."
    ),
}


def validate_scope_settings(
    updates: dict[str, int | str | bool | None], settings: Settings | None = None
) -> None:
    active_settings = settings or Settings()
    positive_int_fields = {"retrieval_top_k", "summary_batch_size"}
    optional_positive_int_fields = {"raw_message_retention_days", "knowledge_retention_days"}
    for field in positive_int_fields:
        value = updates.get(field)
        if field in updates and (not isinstance(value, int) or value < 1):
            raise ValueError(f"{field} must be a positive integer")
    for field in optional_positive_int_fields:
        value = updates.get(field)
        if field in updates and value is not None and (not isinstance(value, int) or value < 1):
            raise ValueError(f"{field} must be a positive integer or off")
    model_fields = {"summary_model", "chat_model", "embedding_model"}
    for field in model_fields:
        if field in updates and updates[field] is not None:
            value = str(updates[field]).strip()
            if not value:
                raise ValueError(f"{field} cannot be empty")
            if field == "chat_model":
                value = active_settings.validate_chat_model(value)
            elif field == "embedding_model":
                value = active_settings.validate_embedding_model(value)
            updates[field] = value
    short_name_fields = {"system_prompt_template", "provider_profile", "reasoning_level"}
    for field in short_name_fields:
        if field in updates and updates[field] is not None:
            value = str(updates[field]).strip().lower()
            if not value:
                raise ValueError(f"{field} cannot be empty")
            if field == "reasoning_level":
                value = active_settings.validate_reasoning_level(value)
            updates[field] = value
    if "review_before_compact" in updates and not isinstance(
        updates["review_before_compact"], bool
    ):
        raise ValueError("review_before_compact must be true or false")


def validate_runtime_settings_values(values: dict[str, Any], settings: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if key not in SAFE_RUNTIME_SETTING_TYPES:
            raise ValueError(f"unknown runtime setting: {key}")
        kind = SAFE_RUNTIME_SETTING_TYPES[key]
        if kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            out[key] = value
        elif kind == "positive_int":
            clean = int(value)
            if clean < 1:
                raise ValueError(f"{key} must be >= 1")
            out[key] = clean
        elif kind == "optional_positive_int":
            if value is None:
                out[key] = None
            else:
                clean = int(value)
                if clean < 1:
                    raise ValueError(f"{key} must be >= 1 or off")
                out[key] = clean
        elif kind == "non_negative_float":
            clean_float = float(value)
            if clean_float < 0:
                raise ValueError(f"{key} must be >= 0")
            out[key] = clean_float
        else:
            clean_str = str(value).strip()
            if not clean_str:
                raise ValueError(f"{key} cannot be empty")
            out[key] = clean_str
    if "provider_profile" in out:
        settings.provider_profile_by_name(str(out["provider_profile"]))
    if "chat_model" in out:
        settings.validate_chat_model(str(out["chat_model"]))
    if "summary_model" in out:
        settings.validate_chat_model(str(out["summary_model"]))
    if "embedding_model" in out:
        settings.validate_embedding_model(str(out["embedding_model"]))
    if "reasoning_level" in out:
        settings.validate_reasoning_level(str(out["reasoning_level"]))
    return out


class MemorySettingsService:
    def __init__(self, *, settings: Settings, session_factory: async_sessionmaker) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self._runtime_overrides: dict[str, Any] = {}

    async def set_scope_metadata(
        self,
        scope: Scope,
        *,
        chat_title: str | None = None,
        topic_title: str | None = None,
        chat_username: str | None = None,
    ) -> None:
        async with session_scope(self.session_factory) as session:
            await ScopeMetadataRepository(session).upsert(
                scope, chat_title=chat_title, topic_title=topic_title, chat_username=chat_username
            )

    async def get_scope_settings(self, scope: Scope) -> EffectiveScopeSettings:
        async with session_scope(self.session_factory) as session:
            return await ScopeSettingsRepository(session).effective(
                scope, self.default_scope_settings()
            )

    async def set_scope_settings(
        self,
        scope: Scope,
        updates: dict[str, int | str | bool | None],
        *,
        updated_by: int | None = None,
    ) -> EffectiveScopeSettings:
        validate_scope_settings(updates, self.settings)
        async with session_scope(self.session_factory) as session:
            repo = ScopeSettingsRepository(session)
            await repo.set_settings(scope, updates, updated_by)
            await EventRepository(session).add(
                event_type="scope_settings", scope=scope, user_id=updated_by, payload=dict(updates)
            )
            return await repo.effective(scope, self.default_scope_settings())

    def settings_token_hash(self, scope: Scope, user_id: int, token: str) -> str:
        payload = f"settings:{scope.key}:{user_id}:{token}".encode()
        return hashlib.sha256(payload).hexdigest()

    async def preview_scope_settings(
        self, scope: Scope, updates: dict[str, int | str | bool | None], *, user_id: int
    ) -> SettingsChangePreview:
        validate_scope_settings(updates, self.settings)
        token = token_urlsafe(8)
        expires_at = utc_now() + timedelta(
            seconds=int(self.runtime_setting("forget_confirm_ttl_seconds"))
        )
        async with session_scope(self.session_factory) as session:
            current = await ScopeSettingsRepository(session).effective(
                scope, self.default_scope_settings()
            )
            await SettingsChallengeRepository(session).create(
                scope=scope,
                user_id=user_id,
                token_hash=self.settings_token_hash(scope, user_id, token),
                updates=dict(updates),
                expires_at=expires_at,
            )
            await EventRepository(session).add(
                event_type="scope_settings_preview",
                scope=scope,
                user_id=user_id,
                payload={"updates": dict(updates), "expires_at": expires_at.isoformat()},
            )
            current_values = {key: current.as_dict().get(key) for key in updates}
        return SettingsChangePreview(
            scope=scope,
            updates=dict(updates),
            current_values=current_values,
            token=token,
            expires_at=expires_at,
            applied=False,
        )

    async def confirm_scope_settings(
        self, scope: Scope, *, user_id: int, confirm: str
    ) -> SettingsChangePreview:
        async with session_scope(self.session_factory) as session:
            challenge_repo = SettingsChallengeRepository(session)
            challenge = await challenge_repo.latest_active(scope=scope, user_id=user_id)
            if challenge is None or challenge.token_hash != self.settings_token_hash(
                scope, user_id, confirm
            ):
                raise ValueError(
                    "No matching pending settings confirmation. Re-run /settings with the "
                    "desired key=value changes."
                )
            updates = dict(challenge.updates)
            validate_scope_settings(updates, self.settings)
            settings_repo = ScopeSettingsRepository(session)
            current = await settings_repo.effective(scope, self.default_scope_settings())
            await settings_repo.set_settings(scope, updates, user_id)
            await challenge_repo.consume(challenge.id)
            await EventRepository(session).add(
                event_type="scope_settings_confirmed",
                scope=scope,
                user_id=user_id,
                payload={"updates": updates, "challenge_id": challenge.id},
            )
            current_values = {key: current.as_dict().get(key) for key in updates}
        return SettingsChangePreview(
            scope=scope,
            updates=updates,
            current_values=current_values,
            token=None,
            expires_at=None,
            applied=True,
        )

    async def refresh_runtime_settings(self) -> dict[str, Any]:
        async with session_scope(self.session_factory) as session:
            values = await RuntimeSettingRepository(session).values()
        self._runtime_overrides = validate_runtime_settings_values(values, self.settings)
        return dict(self._runtime_overrides)

    async def set_runtime_settings(
        self, updates: dict[str, Any], *, updated_by: int | None = None
    ) -> list[RuntimeSettingPreview]:
        validated = validate_runtime_settings_values(updates, self.settings)
        async with session_scope(self.session_factory) as session:
            repo = RuntimeSettingRepository(session)
            rows: list[RuntimeSettingView] = []
            for key, value in validated.items():
                rows.append(await repo.set_value(key=key, value=value, updated_by=updated_by))
            await EventRepository(session).add(
                event_type="runtime_settings_update",
                user_id=updated_by,
                payload={"updates": validated},
            )
        await self.refresh_runtime_settings()
        return [self._runtime_setting_preview(row) for row in rows]

    async def list_runtime_settings(self) -> list[RuntimeSettingPreview]:
        async with session_scope(self.session_factory) as session:
            rows = await RuntimeSettingRepository(session).list_settings()
        stored = {row.key: row for row in rows}
        previews: list[RuntimeSettingPreview] = []
        for key in sorted(SAFE_RUNTIME_SETTING_TYPES):
            row = stored.get(key)
            if row is None:
                previews.append(
                    RuntimeSettingPreview(
                        key=key,
                        value=self.runtime_setting(key),
                        default_value=self.runtime_default(key),
                        previous_value=None,
                        updated_by=None,
                        updated_at=None,
                        version=None,
                    )
                )
            else:
                previews.append(self._runtime_setting_preview(row))
        return previews

    def runtime_default(self, key: str) -> Any:
        defaults = {
            "learning_default_enabled": self.settings.learning_default_enabled,
            "retrieval_candidate_limit": self.settings.retrieval_candidate_limit,
            "retrieval_top_k": self.settings.retrieval_top_k,
            "summary_batch_size": self.settings.summary_batch_size,
            "summary_idle_seconds": self.settings.summary_idle_seconds,
            "summary_model": self.settings.chat_model,
            "system_prompt_template": self.settings.system_prompt_template,
            "provider_profile": self.settings.ai_provider_profile,
            "chat_model": self.settings.chat_model,
            "embedding_model": self.settings.embedding_model,
            "reasoning_level": self.settings.reasoning_level,
            "answer_min_score": self.settings.answer_min_score,
            "forget_confirm_ttl_seconds": self.settings.forget_confirm_ttl_seconds,
            "raw_message_retention_days": self.settings.raw_message_retention_days,
            "knowledge_retention_days": self.settings.knowledge_retention_days,
            "review_before_compact": self.settings.review_before_compact,
        }
        if key not in defaults:
            raise ValueError(f"unknown runtime setting: {key}")
        return defaults[key]

    def runtime_setting(self, key: str) -> Any:
        return self._runtime_overrides.get(key, self.runtime_default(key))

    def _runtime_setting_preview(self, row: RuntimeSettingView) -> RuntimeSettingPreview:
        return RuntimeSettingPreview(
            key=row.key,
            value=row.value,
            default_value=self.runtime_default(row.key),
            previous_value=row.previous_value,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
            version=row.version,
        )

    def default_scope_settings(self) -> EffectiveScopeSettings:
        provider_profile = str(self.runtime_setting("provider_profile"))
        default_profile = self.settings.provider_profile_by_name(provider_profile)
        return EffectiveScopeSettings(
            retrieval_top_k=int(self.runtime_setting("retrieval_top_k")),
            summary_batch_size=int(self.runtime_setting("summary_batch_size")),
            summary_model=str(self.runtime_setting("summary_model")),
            system_prompt_template=str(self.runtime_setting("system_prompt_template")),
            provider_profile=default_profile.name,
            chat_model=str(self.runtime_setting("chat_model")),
            embedding_model=str(self.runtime_setting("embedding_model")),
            reasoning_level=str(self.runtime_setting("reasoning_level")),
            raw_message_retention_days=self.runtime_setting("raw_message_retention_days"),
            knowledge_retention_days=self.runtime_setting("knowledge_retention_days"),
            review_before_compact=bool(self.runtime_setting("review_before_compact")),
        )

    async def set_memory_profile(
        self,
        name: str,
        source_scopes: list[Scope],
        *,
        description: str | None = None,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        if not source_scopes:
            raise ValueError("profile must include at least one source scope")
        async with session_scope(self.session_factory) as session:
            profile = await MemoryProfileRepository(session).upsert_profile(
                name=name,
                source_scopes=source_scopes,
                description=description,
                updated_by=updated_by,
            )
            await EventRepository(session).add(
                event_type="memory_profile_set",
                user_id=updated_by,
                payload={
                    "profile": profile.name,
                    "source_scopes": [scope.key for scope in profile.source_scopes],
                },
            )
            return profile

    async def list_memory_profiles(self) -> list[MemoryProfileView]:
        async with session_scope(self.session_factory) as session:
            return await MemoryProfileRepository(session).list_profiles()

    async def allow_memory_profile(
        self,
        *,
        destination: Scope,
        profile_name: str,
        can_query: bool,
        updated_by: int | None = None,
    ) -> MemoryProfileView:
        async with session_scope(self.session_factory) as session:
            repo = MemoryProfileRepository(session)
            profile = await repo.get_profile(profile_name)
            if profile is None:
                raise ValueError(f"profile {profile_name!r} was not found")
            for source in profile.source_scopes:
                await repo.set_permission(
                    destination=destination,
                    source=source,
                    can_query=can_query,
                    updated_by=updated_by,
                )
            await EventRepository(session).add(
                event_type="memory_profile_permission",
                scope=destination,
                user_id=updated_by,
                payload={"profile": profile.name, "can_query": can_query},
            )
            return profile

    async def set_summary_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SummaryTemplatePreview:
        async with session_scope(self.session_factory) as session:
            row = await SummaryTemplateRepository(session).upsert(
                name=name, prompt=prompt, updated_by=updated_by
            )
            await EventRepository(session).add(
                event_type="summary_template_set",
                user_id=updated_by,
                payload={"template": row.name},
            )
            return SummaryTemplatePreview(row.name, row.prompt, False)

    async def list_summary_templates(self) -> list[SummaryTemplatePreview]:
        previews = [
            SummaryTemplatePreview(name, prompt, True)
            for name, prompt in sorted(BUILTIN_SUMMARY_TEMPLATES.items())
        ]
        async with session_scope(self.session_factory) as session:
            for row in await SummaryTemplateRepository(session).list_templates():
                previews.append(SummaryTemplatePreview(row.name, row.prompt, False))
        return previews

    async def set_system_prompt_template(
        self, name: str, prompt: str, *, updated_by: int | None = None
    ) -> SystemPromptTemplatePreview:
        async with session_scope(self.session_factory) as session:
            row = await SystemPromptTemplateRepository(session).upsert(
                name=name, prompt=prompt, version=PROMPT_VERSION, updated_by=updated_by
            )
            await EventRepository(session).add(
                event_type="system_prompt_template_set",
                user_id=updated_by,
                payload={"template": row.name, "version": row.version},
            )
            return SystemPromptTemplatePreview(row.name, row.version, row.prompt, False)

    async def list_system_prompt_templates(self) -> list[SystemPromptTemplatePreview]:
        previews = [
            SystemPromptTemplatePreview(template.name, template.version, template.prompt, True)
            for template in sorted(BUILTIN_SYSTEM_PROMPTS.values(), key=lambda item: item.name)
        ]
        async with session_scope(self.session_factory) as session:
            for row in await SystemPromptTemplateRepository(session).list_templates():
                previews.append(
                    SystemPromptTemplatePreview(row.name, row.version, row.prompt, False)
                )
        return previews

    async def system_prompt_template_in_session(
        self, session: AsyncSession, name: str
    ) -> PromptTemplate:
        clean_name = name.strip().lower()
        builtin = BUILTIN_SYSTEM_PROMPTS.get(clean_name)
        if builtin is not None:
            return builtin
        row = await SystemPromptTemplateRepository(session).get(clean_name)
        if row is None:
            raise ValueError(f"system prompt template {name!r} was not found")
        return PromptTemplate(name=row.name, version=row.version, prompt=row.prompt)

    async def answer_system_prompt_in_session(
        self,
        session: AsyncSession,
        scope_settings: EffectiveScopeSettings,
        options: AIRequestOptions | None,
    ) -> PromptTemplate:
        request = options or AIRequestOptions()
        if request.system_prompt_override:
            return PromptTemplate(
                name="one_shot_override",
                version=PROMPT_VERSION,
                prompt=request.system_prompt_override.strip(),
            )
        template_name = request.system_prompt_template or scope_settings.system_prompt_template
        return await self.system_prompt_template_in_session(session, template_name)
