from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.authorization import (
    CAPABILITY_BOT_ADMIN,
    CAPABILITY_CHAT_ADMIN,
    ControlPlaneAuthorizationService,
)
from pzp_ai_bot.db.repos.control_plane import ControlPlaneAccessRepository
from pzp_ai_bot.db.session import create_schema, create_session_factory, session_scope
from pzp_ai_bot.telegram.admin import AdminService

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        bot_admins="111",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'authorization.db'}",
        memory_dir=tmp_path / "memory",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
    )


class NativeAdminBot:
    async def get_chat_administrators(self, chat_id: int):
        assert chat_id == -100123
        return [SimpleNamespace(user=SimpleNamespace(id=444))]


async def test_capabilities_merge_bootstrap_managed_delegated_and_native_admins(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    telegram_admins = AdminService(settings.bot_admin_ids, ttl_seconds=300)
    authorization = ControlPlaneAuthorizationService(factory, telegram_admins=telegram_admins)
    async with session_scope(factory) as session:
        repository = ControlPlaneAccessRepository(session)
        await repository.upsert_managed_chat(
            chat_id=-100123,
            title="Test group",
            username=None,
            status="active",
            actor_user_id=111,
            now=NOW,
        )
        await repository.grant_bot_admin(telegram_user_id=222, actor_user_id=111, now=NOW)
        await repository.grant_chat_admin(
            chat_id=-100123, telegram_user_id=333, actor_user_id=111, now=NOW
        )

    bootstrap = await authorization.resolve(111)
    managed = await authorization.resolve(222)
    delegated = await authorization.resolve(333)
    native = await authorization.resolve(444, bot=NativeAdminBot(), chat_id=-100123)

    assert CAPABILITY_BOT_ADMIN in bootstrap.capabilities
    assert CAPABILITY_BOT_ADMIN in managed.capabilities
    assert managed.is_bot_admin
    assert delegated.capabilities_for_chat(-100123) == {CAPABILITY_CHAT_ADMIN}
    assert native.capabilities_for_chat(-100123) == {CAPABILITY_CHAT_ADMIN}

    async with session_scope(factory) as session:
        await ControlPlaneAccessRepository(session).revoke_bot_admin(
            telegram_user_id=222, actor_user_id=111, now=NOW
        )

    removed = await authorization.resolve(222)
    assert not removed.is_bot_admin
    assert CAPABILITY_BOT_ADMIN not in removed.capabilities

    await factory.kw["bind"].dispose()


async def test_delegated_admin_loses_access_when_managed_chat_is_paused(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    authorization = ControlPlaneAuthorizationService(
        factory, telegram_admins=AdminService(settings.bot_admin_ids)
    )
    async with session_scope(factory) as session:
        repository = ControlPlaneAccessRepository(session)
        await repository.upsert_managed_chat(
            chat_id=-100123,
            title="Test group",
            username=None,
            status="active",
            actor_user_id=111,
            now=NOW,
        )
        await repository.grant_chat_admin(
            chat_id=-100123, telegram_user_id=333, actor_user_id=111, now=NOW
        )
        await repository.upsert_managed_chat(
            chat_id=-100123,
            title="Test group",
            username=None,
            status="paused",
            actor_user_id=111,
            now=NOW,
        )

    actor = await authorization.resolve(333)

    assert actor.capabilities_for_chat(-100123) == set()
    await factory.kw["bind"].dispose()
