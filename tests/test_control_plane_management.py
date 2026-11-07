from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pzp_ai_bot.app_context import build_context
from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.auth.identity import TelegramIdentity
from pzp_ai_bot.control_plane.auth.sessions import SessionAuthenticationError
from pzp_ai_bot.control_plane.management import (
    ControlPlaneManagementConflictError,
    ControlPlaneManagementError,
)
from pzp_ai_bot.db.session import upgrade_schema
from pzp_ai_bot.memory.scope import Scope
from pzp_ai_bot.memory.service import IncomingMessage

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def control_plane_settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "telegram_bot_token": "123456:telegram-secret",
            "bot_admins": "111",
            "db_url": f"sqlite+aiosqlite:///{tmp_path / 'management.db'}",
            "memory_dir": tmp_path / "memory",
            "ai_provider": "fake",
            "control_plane_enabled": True,
            "control_plane_public_origin": "https://control.example.test",
            "telegram_oidc_client_id": "123456789",
            "telegram_oidc_client_secret": "oidc-secret",
            "control_plane_master_keys": '{"test":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
            "control_plane_active_master_key_id": "test",
        }
    )


def identity(user_id: int) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=user_id,
        first_name=f"User {user_id}",
        last_name=None,
        username=f"user{user_id}",
        language_code="en",
        photo_url=None,
        is_bot=False,
        is_premium=None,
        authenticated_at=NOW,
    )


class ManagementBotDouble:
    def __init__(self) -> None:
        self.saved_buttons: list[tuple[int, Any]] = []
        self.sent_messages: list[tuple[int, str, Any]] = []
        self.left_chats: list[int] = []
        self.chat_members: dict[tuple[int, int], Any] = {}
        self.chats: dict[int, Any] = {}

    async def save_prepared_keyboard_button(self, user_id: int, button: Any) -> Any:
        self.saved_buttons.append((user_id, button))
        return SimpleNamespace(id="prepared-chat-button")

    async def send_message(self, user_id: int, text: str, reply_markup: Any) -> None:
        self.sent_messages.append((user_id, text, reply_markup))

    async def get_chat(self, chat_id: int) -> Any:
        return self.chats[chat_id]

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any:
        return self.chat_members[(chat_id, user_id)]

    async def get_me(self) -> Any:
        return SimpleNamespace(id=999, is_bot=True)

    async def leave_chat(self, chat_id: int) -> bool:
        self.left_chats.append(chat_id)
        return True


def test_revisions_0016_and_0017_normalize_chats_and_version_scope_settings(tmp_path: Path) -> None:
    settings = control_plane_settings(tmp_path)
    upgrade_schema(settings, "0015_admin_alert_outbox")
    assert settings.sqlite_path is not None
    timestamp = NOW.isoformat()
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute(
            """
            insert into managed_chats (
                chat_id, title, username, status, enrollment_token_hash,
                enrollment_expires_at, created_by, created_at, updated_by,
                updated_at, paused_at, removed_at, version
            ) values (?, ?, null, 'removed', null, null, ?, ?, ?, ?, null, ?, 1)
            """,
            (-1001, "Detached legacy chat", 111, timestamp, 111, timestamp, timestamp),
        )
        connection.execute(
            """
            insert into scope_settings (
                chat_id, message_thread_id, updated_by, updated_at
            ) values (?, null, ?, ?)
            """,
            (-1001, 111, timestamp),
        )
        connection.commit()

    upgrade_schema(settings)

    with sqlite3.connect(settings.sqlite_path) as connection:
        revision = connection.execute("select version_num from alembic_version").fetchone()
        status = connection.execute(
            "select status from managed_chats where chat_id = -1001"
        ).fetchone()
        scope_version = connection.execute(
            "select version from scope_settings where chat_id = -1001"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'index'"
            ).fetchall()
        }

    assert revision == ("0019_embedding_spaces",)
    assert status == ("detached",)
    assert scope_version == (1,)
    assert "managed_chat_enrollment_requests" in tables
    assert {
        "uq_managed_chat_enrollment_requests_request_id",
        "ix_managed_chat_enrollment_requests_requester_status",
    } <= indexes


@pytest.mark.asyncio
async def test_managed_bot_admins_reload_immediately_and_revocation_invalidates_sessions(
    tmp_path: Path,
) -> None:
    context = await build_context(control_plane_settings(tmp_path))
    assert context.control_plane is not None
    management = context.control_plane.management
    issued = await context.control_plane.sessions.issue(
        identity(222), client_kind="browser", now=NOW
    )

    granted = await management.grant_bot_admin(telegram_user_id=222, actor_user_id=111, now=NOW)

    assert granted.telegram_user_id == 222
    assert granted.source == "managed"
    assert granted.version == 1
    assert context.admins.is_bot_admin(222)
    assert (await context.control_plane.authorization.resolve(222)).is_bot_admin
    listed = await management.list_bot_admins()
    assert [(item.telegram_user_id, item.source) for item in listed] == [
        (111, "bootstrap"),
        (222, "managed"),
    ]

    revoked = await management.revoke_bot_admin(
        telegram_user_id=222, expected_version=1, actor_user_id=111, now=NOW
    )

    assert revoked.version == 2
    assert not context.admins.is_bot_admin(222)
    with pytest.raises(SessionAuthenticationError):
        await context.control_plane.sessions.authenticate(issued.session_token, now=NOW)

    with pytest.raises(ControlPlaneManagementError, match="bootstrap") as immutable:
        await management.revoke_bot_admin(
            telegram_user_id=111, expected_version=1, actor_user_id=111, now=NOW
        )
    assert immutable.value.code == "bootstrap_owner_immutable"


@pytest.mark.asyncio
async def test_admin_grant_mutations_use_optimistic_versions_and_verify_delegated_membership(
    tmp_path: Path,
) -> None:
    context = await build_context(control_plane_settings(tmp_path))
    assert context.control_plane is not None
    management = context.control_plane.management
    bot = ManagementBotDouble()
    bot.chats[-1001] = SimpleNamespace(
        id=-1001, type="supergroup", title="Engineering", username="eng"
    )
    bot.chat_members[(-1001, 333)] = SimpleNamespace(
        status="member", user=SimpleNamespace(id=333, is_bot=False)
    )
    await management.enroll_chat(
        chat_id=-1001, title="Engineering", username="eng", actor_user_id=111, now=NOW
    )

    grant = await management.grant_chat_admin(
        chat_id=-1001, telegram_user_id=333, actor_user_id=111, bot=bot, now=NOW
    )
    assert grant.version == 1
    assert (await context.control_plane.authorization.resolve(333)).delegated_chat_ids == frozenset(
        {-1001}
    )
    assert await context.admins.is_chat_admin(bot, -1001, 333)

    revoked = await management.revoke_chat_admin(
        chat_id=-1001, telegram_user_id=333, expected_version=1, actor_user_id=111, now=NOW
    )
    assert revoked.version == 2
    assert not await context.admins.is_chat_admin(bot, -1001, 333)
    with pytest.raises(ControlPlaneManagementConflictError) as stale:
        await management.revoke_chat_admin(
            chat_id=-1001, telegram_user_id=333, expected_version=1, actor_user_id=111, now=NOW
        )
    assert stale.value.current_version == 2

    bot.chat_members[(-1001, 444)] = SimpleNamespace(
        status="member", user=SimpleNamespace(id=444, is_bot=True)
    )
    with pytest.raises(ControlPlaneManagementError) as bot_target:
        await management.grant_chat_admin(
            chat_id=-1001, telegram_user_id=444, actor_user_id=111, bot=bot, now=NOW
        )
    assert bot_target.value.code == "delegated_admin_target_invalid"


@pytest.mark.asyncio
async def test_prepared_request_chat_enrollment_is_bound_to_the_requesting_admin(
    tmp_path: Path,
) -> None:
    context = await build_context(control_plane_settings(tmp_path))
    assert context.control_plane is not None
    management = context.control_plane.management
    bot = ManagementBotDouble()
    bot.chats[-1002] = SimpleNamespace(
        id=-1002, type="supergroup", title="Operations", username="ops"
    )
    bot.chat_members[(-1002, 111)] = SimpleNamespace(
        status="administrator", user=SimpleNamespace(id=111, is_bot=False)
    )
    bot.chat_members[(-1002, 999)] = SimpleNamespace(
        status="member", user=SimpleNamespace(id=999, is_bot=True)
    )

    pending = await management.create_chat_enrollment(
        telegram_user_id=111, bot=bot, delivery="mini_app", now=NOW
    )

    assert pending.status == "pending"
    assert pending.prepared_button_id == "prepared-chat-button"
    saved_user_id, saved = bot.saved_buttons[0]
    assert saved_user_id == 111
    assert saved.request_chat.request_id == pending.request_id
    assert saved.request_chat.chat_is_channel is False
    assert saved.request_chat.bot_is_member is True
    assert saved.request_chat.request_title is True
    assert saved.request_chat.request_username is True

    with pytest.raises(ControlPlaneManagementError) as wrong_user:
        await management.complete_chat_enrollment(
            request_id=pending.request_id,
            telegram_user_id=222,
            chat_id=-1002,
            shared_title="Operations",
            shared_username="ops",
            bot=bot,
            now=NOW,
        )
    assert wrong_user.value.code == "chat_enrollment_request_mismatch"

    complete = await management.complete_chat_enrollment(
        request_id=pending.request_id,
        telegram_user_id=111,
        chat_id=-1002,
        shared_title="Operations",
        shared_username="ops",
        bot=bot,
        now=NOW,
    )

    assert complete.status == "completed"
    assert complete.chat_id == -1002
    chats = await management.list_managed_chats()
    assert [(chat.chat_id, chat.status, chat.title) for chat in chats] == [
        (-1002, "active", "Operations")
    ]


@pytest.mark.asyncio
async def test_managed_chat_lifecycle_is_versioned_and_settings_report_precedence(
    tmp_path: Path,
) -> None:
    context = await build_context(control_plane_settings(tmp_path))
    assert context.control_plane is not None
    management = context.control_plane.management
    enrolled = await management.enroll_chat(
        chat_id=-1003, title="Architecture", username=None, actor_user_id=111, now=NOW
    )
    assert enrolled.status == "active"
    assert await management.allows_ingestion(-1003)

    paused = await management.set_chat_status(
        chat_id=-1003, status="paused", expected_version=1, actor_user_id=111, now=NOW
    )
    assert paused.version == 2
    assert not await management.allows_ingestion(-1003)
    with pytest.raises(ControlPlaneManagementConflictError):
        await management.set_chat_status(
            chat_id=-1003, status="active", expected_version=1, actor_user_id=111, now=NOW
        )

    await context.memory.set_runtime_settings({"retrieval_top_k": 8}, updated_by=111)
    await context.memory.set_scope_settings(
        Scope.global_scope(), {"summary_batch_size": 9}, updated_by=111
    )
    await context.memory.set_scope_settings(
        Scope(-1003, None), {"retrieval_top_k": 4}, updated_by=111
    )
    await context.memory.set_scope_settings(
        Scope(-1003, 7), {"reasoning_level": "high"}, updated_by=111
    )

    settings = await management.scope_settings(Scope(-1003, 7))
    assert settings.effective["retrieval_top_k"] == 4
    assert settings.effective["summary_batch_size"] == 9
    assert settings.effective["reasoning_level"] == "high"
    assert settings.overrides == {"reasoning_level": "high"}
    assert settings.inherited["retrieval_top_k"] == 4
    assert settings.inherited["summary_batch_size"] == 9


@pytest.mark.asyncio
async def test_detach_leave_and_forget_have_distinct_persistence_effects(tmp_path: Path) -> None:
    context = await build_context(control_plane_settings(tmp_path))
    assert context.control_plane is not None
    management = context.control_plane.management
    bot = ManagementBotDouble()
    scope = Scope(-1004, None)
    await management.enroll_chat(
        chat_id=-1004, title="Persistence", username=None, actor_user_id=111, now=NOW
    )
    await context.memory.set_learning(scope, True, updated_by=111)
    await context.memory.ingest(
        IncomingMessage(
            telegram_message_id=1,
            scope=scope,
            user_id=111,
            username="owner",
            text="Durable test memory",
            created_at=NOW,
        )
    )

    detached = await management.set_chat_status(
        chat_id=-1004, status="detached", expected_version=1, actor_user_id=111, now=NOW
    )
    assert detached.status == "detached"
    assert (await context.memory.memory_status(scope)).messages == 1

    resumed = await management.set_chat_status(
        chat_id=-1004, status="active", expected_version=2, actor_user_id=111, now=NOW
    )
    left = await management.leave_chat(
        chat_id=-1004, expected_version=resumed.version, actor_user_id=111, bot=bot, now=NOW
    )
    assert left.status == "detached"
    assert bot.left_chats == [-1004]
    assert (await context.memory.memory_status(scope)).messages == 1

    forgotten = await management.forget_chat(chat_id=-1004, actor_user_id=111)
    assert forgotten == {"messages": 1, "knowledge_items": 0, "embeddings": 1}
    status = await context.memory.memory_status(scope)
    assert status.messages == 0
    assert status.embeddings == 0


@pytest.mark.asyncio
async def test_only_active_managed_chats_ingest_or_run_automatic_summary_work(
    tmp_path: Path,
) -> None:
    context = await build_context(control_plane_settings(tmp_path))
    assert context.control_plane is not None
    management = context.control_plane.management
    scope = Scope(-1005, None)
    await context.memory.set_learning(scope, True, updated_by=111)

    before_enrollment = await context.memory.ingest(
        IncomingMessage(1, scope, 111, "owner", "not managed", NOW)
    )
    assert before_enrollment.reason == "managed_chat_inactive"
    assert (await context.memory.memory_status(scope)).messages == 0

    active = await management.enroll_chat(
        chat_id=-1005, title="Lifecycle gate", username=None, actor_user_id=111, now=NOW
    )
    learned = await context.memory.ingest(
        IncomingMessage(2, scope, 111, "owner", "managed and learnable", NOW)
    )
    assert learned.learned is True
    paused = await management.set_chat_status(
        chat_id=-1005, status="paused", expected_version=active.version, actor_user_id=111, now=NOW
    )
    while_paused = await context.memory.ingest(
        IncomingMessage(3, scope, 111, "owner", "must not be stored", NOW)
    )
    assert while_paused.reason == "managed_chat_inactive"
    assert (await context.memory.memory_status(scope)).messages == 1
    assert await context.memory.summarize_now(scope) == 0

    await management.set_chat_status(
        chat_id=-1005, status="active", expected_version=paused.version, actor_user_id=111, now=NOW
    )
    assert await context.memory.summarize_now(scope) == 1
