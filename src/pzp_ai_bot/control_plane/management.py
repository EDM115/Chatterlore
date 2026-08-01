from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram.types import KeyboardButton, KeyboardButtonRequestChat, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.db.models import BotAdminGrant, ChatAdminGrant, ManagedChat
from pzp_ai_bot.db.repos.control_plane import (
    ControlPlaneAccessRepository,
    ControlPlaneSessionRepository,
)
from pzp_ai_bot.db.repos.events import EventRepository
from pzp_ai_bot.db.repos.settings import ScopeSettingsRepository
from pzp_ai_bot.db.session import session_scope
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import MemoryService
from pzp_ai_bot.telegram.admin import AdminService

from .auth.sessions import ControlPlaneSessionService

CHAT_ENROLLMENT_TTL_SECONDS = 120
MANAGED_CHAT_STATUSES = frozenset({"active", "paused", "detached"})


class ControlPlaneManagementError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ControlPlaneManagementConflictError(ControlPlaneManagementError):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__("version_conflict", "the resource changed concurrently")


@dataclass(frozen=True, slots=True)
class BotAdminView:
    telegram_user_id: int
    source: str
    version: int | None
    granted_by: int | None
    granted_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChatAdminView:
    chat_id: int
    telegram_user_id: int
    version: int
    granted_by: int
    granted_at: datetime
    active: bool


@dataclass(frozen=True, slots=True)
class ManagedChatView:
    chat_id: int
    title: str | None
    username: str | None
    status: str
    version: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChatEnrollmentView:
    request_id: int
    status: str
    delivery: str
    expires_at: datetime
    prepared_button_id: str | None = None
    chat_id: int | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ScopeSettingsView:
    scope: str
    effective: dict[str, Any]
    inherited: dict[str, Any]
    overrides: dict[str, Any]


class ControlPlaneManagementService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        admins: AdminService,
        sessions: ControlPlaneSessionService,
        memory: MemoryService,
    ) -> None:
        self.session_factory = session_factory
        self.admins = admins
        self.sessions = sessions
        self.memory = memory

    async def refresh_managed_authority(self) -> set[int]:
        async with session_scope(self.session_factory) as session:
            repository = ControlPlaneAccessRepository(session)
            user_ids = await repository.active_bot_admin_ids()
            delegated_grants = await repository.active_chat_admin_grants()
            active_chat_ids = await repository.active_managed_chat_ids()
        delegated: dict[int, set[int]] = {}
        for chat_id, user_id in delegated_grants:
            delegated.setdefault(chat_id, set()).add(user_id)
        self.admins.replace_managed_bot_admins(user_ids)
        self.admins.replace_delegated_chat_admins(delegated)
        self.admins.replace_active_managed_chats(active_chat_ids)
        return user_ids

    async def list_bot_admins(self) -> list[BotAdminView]:
        by_user = {
            user_id: BotAdminView(user_id, "bootstrap", None, None, None)
            for user_id in self.admins.bot_admin_ids
        }
        async with session_scope(self.session_factory) as session:
            grants = await ControlPlaneAccessRepository(session).list_bot_admin_grants()
        for grant in grants:
            if grant.revoked_at is None and grant.telegram_user_id not in by_user:
                by_user[grant.telegram_user_id] = _bot_admin_view(grant)
        return [by_user[user_id] for user_id in sorted(by_user)]

    async def grant_bot_admin(
        self, *, telegram_user_id: int, actor_user_id: int, now: datetime | None = None
    ) -> BotAdminView:
        _require_positive_user_id(telegram_user_id)
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            grant = await ControlPlaneAccessRepository(session).grant_bot_admin(
                telegram_user_id=telegram_user_id, actor_user_id=actor_user_id, now=current
            )
            await EventRepository(session).add(
                event_type="control_plane.bot_admin.granted",
                user_id=actor_user_id,
                payload={"target_user_id": telegram_user_id, "version": grant.version},
            )
            view = _bot_admin_view(grant)
        self.admins.add_managed_bot_admin(telegram_user_id)
        return view

    async def revoke_bot_admin(
        self,
        *,
        telegram_user_id: int,
        expected_version: int,
        actor_user_id: int,
        now: datetime | None = None,
    ) -> BotAdminView:
        if telegram_user_id in self.admins.bot_admin_ids:
            raise ControlPlaneManagementError(
                "bootstrap_owner_immutable", "bootstrap bot administrators cannot be removed"
            )
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            repository = ControlPlaneAccessRepository(session)
            grant = await repository.get_bot_admin_grant(telegram_user_id)
            if grant is None:
                raise ControlPlaneManagementError(
                    "bot_admin_not_found", "the managed bot administrator was not found"
                )
            if grant.version != expected_version:
                raise ControlPlaneManagementConflictError(grant.version)
            active_ids = await repository.active_bot_admin_ids()
            effective_after = (self.admins.bot_admin_ids | active_ids) - {telegram_user_id}
            if not effective_after:
                raise ControlPlaneManagementError(
                    "last_owner_required", "at least one bot administrator must remain"
                )
            if grant.revoked_at is None:
                grant.revoked_at = current
                grant.revoked_by = actor_user_id
                grant.version += 1
            await ControlPlaneSessionRepository(session).revoke_user(
                telegram_user_id=telegram_user_id, now=current, reason="bot_admin_revoked"
            )
            await EventRepository(session).add(
                event_type="control_plane.bot_admin.revoked",
                user_id=actor_user_id,
                payload={"target_user_id": telegram_user_id, "version": grant.version},
            )
            await session.flush()
            view = _bot_admin_view(grant)
        self.admins.remove_managed_bot_admin(telegram_user_id)
        return view

    async def list_chat_admins(self, chat_id: int) -> list[ChatAdminView]:
        async with session_scope(self.session_factory) as session:
            grants = await ControlPlaneAccessRepository(session).list_chat_admin_grants(chat_id)
        return [_chat_admin_view(grant) for grant in grants if grant.revoked_at is None]

    async def grant_chat_admin(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        actor_user_id: int,
        bot: Any,
        now: datetime | None = None,
    ) -> ChatAdminView:
        _require_positive_user_id(telegram_user_id)
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            managed = await ControlPlaneAccessRepository(session).get_managed_chat(chat_id)
        if managed is None or managed.status != "active":
            raise ControlPlaneManagementError(
                "managed_chat_inactive", "delegated administrators require an active managed chat"
            )
        try:
            member = await bot.get_chat_member(chat_id, telegram_user_id)
        except Exception as exc:
            raise ControlPlaneManagementError(
                "delegated_admin_target_unverifiable",
                "the Telegram account could not be verified in this chat",
            ) from exc
        target = getattr(member, "user", None)
        if (
            target is None
            or int(getattr(target, "id", 0)) != telegram_user_id
            or bool(getattr(target, "is_bot", False))
            or _member_status(member) in {"left", "kicked"}
        ):
            raise ControlPlaneManagementError(
                "delegated_admin_target_invalid",
                "the Telegram account is not an eligible member of this chat",
            )
        async with session_scope(self.session_factory) as session:
            grant = await ControlPlaneAccessRepository(session).grant_chat_admin(
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                actor_user_id=actor_user_id,
                now=current,
            )
            await EventRepository(session).add(
                event_type="control_plane.chat_admin.granted",
                scope=Scope(chat_id, None),
                user_id=actor_user_id,
                payload={"target_user_id": telegram_user_id, "version": grant.version},
            )
            view = _chat_admin_view(grant)
        self.admins.add_delegated_chat_admin(chat_id, telegram_user_id)
        return view

    async def revoke_chat_admin(
        self,
        *,
        chat_id: int,
        telegram_user_id: int,
        expected_version: int,
        actor_user_id: int,
        now: datetime | None = None,
    ) -> ChatAdminView:
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            repository = ControlPlaneAccessRepository(session)
            grant = await repository.get_chat_admin_grant(chat_id, telegram_user_id)
            if grant is None:
                raise ControlPlaneManagementError(
                    "chat_admin_not_found", "the delegated chat administrator was not found"
                )
            if grant.version != expected_version:
                raise ControlPlaneManagementConflictError(grant.version)
            if grant.revoked_at is None:
                grant.revoked_at = current
                grant.revoked_by = actor_user_id
                grant.version += 1
            await EventRepository(session).add(
                event_type="control_plane.chat_admin.revoked",
                scope=Scope(chat_id, None),
                user_id=actor_user_id,
                payload={"target_user_id": telegram_user_id, "version": grant.version},
            )
            await session.flush()
            view = _chat_admin_view(grant)
        self.admins.remove_delegated_chat_admin(chat_id, telegram_user_id)
        return view

    async def enroll_chat(
        self,
        *,
        chat_id: int,
        title: str | None,
        username: str | None,
        actor_user_id: int,
        now: datetime | None = None,
    ) -> ManagedChatView:
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            managed = await ControlPlaneAccessRepository(session).upsert_managed_chat(
                chat_id=chat_id,
                title=_clean_optional(title),
                username=_clean_optional(username),
                status="active",
                actor_user_id=actor_user_id,
                now=current,
            )
            await EventRepository(session).add(
                event_type="control_plane.managed_chat.enrolled",
                scope=Scope(chat_id, None),
                user_id=actor_user_id,
                payload={"status": "active", "version": managed.version},
            )
            view = _managed_chat_view(managed)
        self.admins.set_managed_chat_active(chat_id, True)
        return view

    async def list_managed_chats(self) -> list[ManagedChatView]:
        async with session_scope(self.session_factory) as session:
            rows = await ControlPlaneAccessRepository(session).list_managed_chats()
        return [_managed_chat_view(row) for row in rows]

    async def get_managed_chat(self, chat_id: int) -> ManagedChatView:
        async with session_scope(self.session_factory) as session:
            row = await ControlPlaneAccessRepository(session).get_managed_chat(chat_id)
        if row is None:
            raise ControlPlaneManagementError(
                "managed_chat_not_found", "the managed chat was not found"
            )
        return _managed_chat_view(row)

    async def set_chat_status(
        self,
        *,
        chat_id: int,
        status: str,
        expected_version: int,
        actor_user_id: int,
        now: datetime | None = None,
    ) -> ManagedChatView:
        if status not in MANAGED_CHAT_STATUSES:
            raise ControlPlaneManagementError(
                "managed_chat_status_invalid", "the managed chat status is invalid"
            )
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            repository = ControlPlaneAccessRepository(session)
            managed = await repository.get_managed_chat(chat_id)
            if managed is None:
                raise ControlPlaneManagementError(
                    "managed_chat_not_found", "the managed chat was not found"
                )
            if managed.version != expected_version:
                raise ControlPlaneManagementConflictError(managed.version)
            before = managed.status
            managed.status = status
            managed.updated_by = actor_user_id
            managed.updated_at = current
            managed.paused_at = current if status == "paused" else None
            managed.removed_at = current if status == "detached" else None
            managed.version += 1
            await EventRepository(session).add(
                event_type="control_plane.managed_chat.status_changed",
                scope=Scope(chat_id, None),
                user_id=actor_user_id,
                payload={"before": before, "after": status, "version": managed.version},
            )
            await session.flush()
            view = _managed_chat_view(managed)
        self.admins.set_managed_chat_active(chat_id, status == "active")
        return view

    async def allows_ingestion(self, chat_id: int) -> bool:
        async with session_scope(self.session_factory) as session:
            managed = await ControlPlaneAccessRepository(session).get_managed_chat(chat_id)
        return managed is not None and managed.status == "active" and managed.removed_at is None

    async def allows_scope(self, scope: Scope) -> bool:
        return scope.chat_id is not None and await self.allows_ingestion(scope.chat_id)

    async def leave_chat(
        self,
        *,
        chat_id: int,
        expected_version: int,
        actor_user_id: int,
        bot: Any,
        now: datetime | None = None,
    ) -> ManagedChatView:
        current = now or datetime.now(UTC)
        managed = await self.get_managed_chat(chat_id)
        if managed.version != expected_version:
            raise ControlPlaneManagementConflictError(managed.version)
        try:
            left = await bot.leave_chat(chat_id)
        except Exception as exc:
            raise ControlPlaneManagementError(
                "managed_chat_leave_failed", "Telegram could not remove the bot from this chat"
            ) from exc
        if not left:
            raise ControlPlaneManagementError(
                "managed_chat_leave_failed", "Telegram did not confirm leaving this chat"
            )
        return await self.set_chat_status(
            chat_id=chat_id,
            status="detached",
            expected_version=expected_version,
            actor_user_id=actor_user_id,
            now=current,
        )

    async def forget_chat(self, *, chat_id: int, actor_user_id: int) -> dict[str, int]:
        await self.get_managed_chat(chat_id)
        return await self.memory.forget_confirmed(Scope(chat_id, None), user_id=actor_user_id)

    async def create_chat_enrollment(
        self, *, telegram_user_id: int, bot: Any, delivery: str, now: datetime | None = None
    ) -> ChatEnrollmentView:
        if delivery not in {"mini_app", "telegram_dm"}:
            raise ControlPlaneManagementError(
                "chat_enrollment_delivery_invalid", "the enrollment delivery is invalid"
            )
        current = now or datetime.now(UTC)
        expires_at = current + timedelta(seconds=CHAT_ENROLLMENT_TTL_SECONDS)
        request_id = secrets.randbelow(2**31 - 1) + 1
        button = KeyboardButton(
            text="Select a group for PZP AI",
            request_chat=KeyboardButtonRequestChat(
                request_id=request_id,
                chat_is_channel=False,
                bot_is_member=True,
                request_title=True,
                request_username=True,
            ),
        )
        async with session_scope(self.session_factory) as session:
            await ControlPlaneAccessRepository(session).add_enrollment_request(
                request_id=request_id,
                requested_by=telegram_user_id,
                delivery=delivery,
                created_at=current,
                expires_at=expires_at,
            )
            await EventRepository(session).add(
                event_type="control_plane.chat_enrollment.created",
                user_id=telegram_user_id,
                payload={"request_id": request_id, "delivery": delivery},
            )
        try:
            prepared = await bot.save_prepared_keyboard_button(telegram_user_id, button)
            if delivery == "telegram_dm":
                await bot.send_message(
                    telegram_user_id,
                    "Select the group to enroll, then return to the control plane.",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True
                    ),
                )
        except Exception as exc:
            async with session_scope(self.session_factory) as session:
                stored = await ControlPlaneAccessRepository(session).get_enrollment_request(
                    request_id
                )
                if stored is not None:
                    stored.status = "failed"
                    stored.error_code = "telegram_delivery_failed"
                    stored.completed_at = current
            raise ControlPlaneManagementError(
                "chat_enrollment_delivery_failed",
                "Telegram could not prepare the chat selection request",
            ) from exc
        async with session_scope(self.session_factory) as session:
            stored = await ControlPlaneAccessRepository(session).get_enrollment_request(request_id)
            assert stored is not None
            stored.prepared_button_id = str(prepared.id)
            await session.flush()
            return _enrollment_view(stored)

    async def get_chat_enrollment(
        self, *, request_id: int, telegram_user_id: int, now: datetime | None = None
    ) -> ChatEnrollmentView:
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            row = await ControlPlaneAccessRepository(session).get_enrollment_request(request_id)
            if row is None or row.requested_by != telegram_user_id:
                raise ControlPlaneManagementError(
                    "chat_enrollment_request_not_found", "the enrollment request was not found"
                )
            if row.status == "pending" and _as_aware(row.expires_at) <= current:
                row.status = "expired"
                row.completed_at = current
                await session.flush()
            return _enrollment_view(row)

    async def complete_chat_enrollment(
        self,
        *,
        request_id: int,
        telegram_user_id: int,
        chat_id: int,
        shared_title: str | None,
        shared_username: str | None,
        bot: Any,
        now: datetime | None = None,
    ) -> ChatEnrollmentView:
        current = now or datetime.now(UTC)
        async with session_scope(self.session_factory) as session:
            row = await ControlPlaneAccessRepository(session).get_enrollment_request(request_id)
            if row is None or row.requested_by != telegram_user_id:
                raise ControlPlaneManagementError(
                    "chat_enrollment_request_mismatch",
                    "the shared chat does not match this enrollment request",
                )
            if row.status != "pending" or _as_aware(row.expires_at) <= current:
                raise ControlPlaneManagementError(
                    "chat_enrollment_request_inactive", "the enrollment request is no longer active"
                )
        try:
            chat = await bot.get_chat(chat_id)
            requester = await bot.get_chat_member(chat_id, telegram_user_id)
            bot_user = await bot.get_me()
            bot_member = await bot.get_chat_member(chat_id, int(bot_user.id))
        except Exception as exc:
            raise ControlPlaneManagementError(
                "chat_enrollment_unverifiable", "Telegram could not verify the selected chat"
            ) from exc
        if str(getattr(chat, "type", "")) not in {"group", "supergroup"}:
            raise ControlPlaneManagementError(
                "chat_enrollment_type_invalid", "only groups and supergroups can be enrolled"
            )
        if _member_status(requester) not in {"administrator", "creator", "owner"}:
            raise ControlPlaneManagementError(
                "chat_enrollment_admin_required", "the requester must administer the selected chat"
            )
        if _member_status(bot_member) in {"left", "kicked"}:
            raise ControlPlaneManagementError(
                "chat_enrollment_bot_missing", "the bot must be a member of the selected chat"
            )
        managed = await self.enroll_chat(
            chat_id=chat_id,
            title=_clean_optional(getattr(chat, "title", None)) or _clean_optional(shared_title),
            username=_clean_optional(getattr(chat, "username", None))
            or _clean_optional(shared_username),
            actor_user_id=telegram_user_id,
            now=current,
        )
        async with session_scope(self.session_factory) as session:
            row = await ControlPlaneAccessRepository(session).get_enrollment_request(request_id)
            assert row is not None
            row.status = "completed"
            row.chat_id = managed.chat_id
            row.completed_at = current
            await EventRepository(session).add(
                event_type="control_plane.chat_enrollment.completed",
                scope=Scope(chat_id, None),
                user_id=telegram_user_id,
                payload={"request_id": request_id},
            )
            await session.flush()
            return _enrollment_view(row)

    async def scope_settings(self, scope: Scope) -> ScopeSettingsView:
        defaults = self.memory._default_scope_settings()
        async with session_scope(self.session_factory) as session:
            repository = ScopeSettingsRepository(session)
            effective = (await repository.effective(scope, defaults)).as_dict()
            exact = await repository.get(scope)
            overrides = (
                {
                    key: getattr(exact, key)
                    for key in sorted(repository.SETTING_FIELDS)
                    if getattr(exact, key) is not None
                }
                if exact is not None
                else {}
            )
            if scope.thread_id is not None:
                inherited_values = (
                    await repository.effective(Scope(scope.chat_id, None), defaults)
                ).as_dict()
            elif scope.chat_id is not None:
                inherited_values = (
                    await repository.effective(Scope.global_scope(), defaults)
                ).as_dict()
            else:
                inherited_values = defaults.as_dict()
        inherited = {key: value for key, value in inherited_values.items() if key not in overrides}
        return ScopeSettingsView(scope.key, effective, inherited, overrides)


def _bot_admin_view(grant: BotAdminGrant) -> BotAdminView:
    return BotAdminView(
        telegram_user_id=grant.telegram_user_id,
        source="managed",
        version=grant.version,
        granted_by=grant.granted_by,
        granted_at=_as_aware(grant.granted_at),
    )


def _chat_admin_view(grant: ChatAdminGrant) -> ChatAdminView:
    return ChatAdminView(
        chat_id=grant.chat_id,
        telegram_user_id=grant.telegram_user_id,
        version=grant.version,
        granted_by=grant.granted_by,
        granted_at=_as_aware(grant.granted_at),
        active=grant.revoked_at is None,
    )


def _managed_chat_view(row: ManagedChat) -> ManagedChatView:
    return ManagedChatView(
        chat_id=row.chat_id,
        title=row.title,
        username=row.username,
        status=row.status,
        version=row.version,
        updated_at=_as_aware(row.updated_at),
    )


def _enrollment_view(row: Any) -> ChatEnrollmentView:
    return ChatEnrollmentView(
        request_id=row.request_id,
        status=row.status,
        delivery=row.delivery,
        expires_at=_as_aware(row.expires_at),
        prepared_button_id=row.prepared_button_id,
        chat_id=row.chat_id,
        error_code=row.error_code,
    )


def _member_status(member: Any) -> str:
    value = getattr(member, "status", "")
    return str(getattr(value, "value", value)).lower()


def _require_positive_user_id(user_id: int) -> None:
    if user_id < 1:
        raise ControlPlaneManagementError(
            "telegram_user_id_invalid", "the Telegram user id must be positive"
        )


def _clean_optional(value: Any) -> str | None:
    clean = str(value).strip() if value is not None else ""
    return clean or None


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
