from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.auth.identity import TelegramIdentity
from pzp_ai_bot.control_plane.auth.sessions import (
    ControlPlaneSessionService,
    SessionAuthenticationError,
    has_fresh_step_up,
)
from pzp_ai_bot.control_plane.auth.transactions import OIDCTransactionError, OIDCTransactionService
from pzp_ai_bot.db.models import ControlPlaneSession, OIDCAuthTransaction
from pzp_ai_bot.db.session import create_schema, create_session_factory, session_scope

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:fake",
        "bot_admins": "111",
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}",
        "memory_dir": tmp_path / "memory",
        "control_plane_enabled": True,
        "control_plane_public_origin": "https://control.example.test",
        "telegram_oidc_client_id": "123456789",
        "telegram_oidc_client_secret": "oidc-secret",
        "control_plane_master_keys": '{"test":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        "control_plane_active_master_key_id": "test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def identity(user_id: int = 887_758_628) -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=user_id,
        first_name="Lu",
        last_name="Lederrey",
        username="lederrey",
        language_code="en",
        photo_url=None,
        is_bot=False,
        is_premium=True,
        authenticated_at=NOW,
    )


async def test_oidc_transactions_encrypt_pkce_and_are_consumed_once(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = OIDCTransactionService(factory, settings)

    issued = await service.create(return_to="/settings?scope=all", now=NOW)

    assert issued.redirect_uri == "https://control.example.test/auth/telegram/callback"
    assert issued.return_to == "/settings?scope=all"
    assert issued.code_challenge_method == "S256"
    assert issued.state
    assert issued.nonce
    async with session_scope(factory) as session:
        stored = await session.scalar(select(OIDCAuthTransaction))
        assert stored is not None
        assert issued.state not in stored.state_hash
        assert issued.code_verifier.encode() not in stored.pkce_ciphertext
        assert stored.redirect_uri == issued.redirect_uri

    consumed = await service.consume(issued.state, now=NOW + timedelta(seconds=1))

    assert consumed.code_verifier == issued.code_verifier
    assert consumed.nonce == issued.nonce
    assert consumed.return_to == issued.return_to
    with pytest.raises(OIDCTransactionError, match="invalid, expired, or already used"):
        await service.consume(issued.state, now=NOW + timedelta(seconds=2))

    await factory.kw["bind"].dispose()


async def test_oidc_transactions_reject_unsafe_returns_and_expiry(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, control_plane_oidc_transaction_ttl_seconds=60)
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = OIDCTransactionService(factory, settings)

    for unsafe in ("https://evil.example/", "//evil.example/", "/ok\\evil", "settings"):
        with pytest.raises(OIDCTransactionError, match="relative"):
            await service.create(return_to=unsafe, now=NOW)

    issued = await service.create(return_to="/", now=NOW)
    with pytest.raises(OIDCTransactionError, match="invalid, expired, or already used"):
        await service.consume(issued.state, now=NOW + timedelta(seconds=61))

    await factory.kw["bind"].dispose()


async def test_sessions_persist_only_hashes_and_enforce_stable_csrf_and_expiry(
    tmp_path: Path,
) -> None:
    settings = settings_for(
        tmp_path, control_plane_session_idle_seconds=120, control_plane_session_absolute_seconds=600
    )
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ControlPlaneSessionService(factory, settings)

    issued = await service.issue(identity(), client_kind="mini_app", now=NOW)

    async with session_scope(factory) as session:
        stored = await session.scalar(select(ControlPlaneSession))
        assert stored is not None
        assert stored.token_hash != issued.session_token
        assert stored.csrf_hash != issued.csrf_token
        assert issued.session_token not in stored.token_hash
        assert issued.csrf_token not in stored.csrf_hash

    active = await service.authenticate(issued.session_token, now=NOW + timedelta(seconds=30))
    assert active.identity.telegram_user_id == 887_758_628
    assert active.client_kind == "mini_app"
    assert await service.validate_csrf(
        issued.session_token, issued.csrf_token, now=NOW + timedelta(seconds=30)
    )

    restarted = ControlPlaneSessionService(factory, settings)
    refreshed_csrf = await restarted.get_csrf_token(
        issued.session_token, now=NOW + timedelta(seconds=31)
    )
    assert refreshed_csrf == issued.csrf_token
    assert await service.validate_csrf(
        issued.session_token, refreshed_csrf, now=NOW + timedelta(seconds=31)
    )
    assert not await service.validate_csrf(
        issued.session_token, "wrong-token", now=NOW + timedelta(seconds=31)
    )

    switched = await service.issue(
        identity(999),
        client_kind="browser",
        now=NOW + timedelta(seconds=32),
        replace_session_token=issued.session_token,
    )
    with pytest.raises(SessionAuthenticationError, match="invalid or expired"):
        await service.authenticate(issued.session_token, now=NOW + timedelta(seconds=33))
    assert (
        await service.authenticate(switched.session_token, now=NOW + timedelta(seconds=33))
    ).identity.telegram_user_id == 999

    with pytest.raises(SessionAuthenticationError, match="invalid or expired"):
        await service.authenticate(switched.session_token, now=NOW + timedelta(seconds=153))

    await factory.kw["bind"].dispose()


async def test_legacy_session_adopts_stable_csrf_once_without_reauthentication(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ControlPlaneSessionService(factory, settings)
    issued = await service.issue(identity(), client_kind="browser", now=NOW)
    async with session_scope(factory) as session:
        stored = await session.scalar(select(ControlPlaneSession))
        assert stored is not None
        stored.csrf_hash = "0" * 64
    first = await service.get_csrf_token(issued.session_token, now=NOW + timedelta(seconds=1))
    second = await service.get_csrf_token(issued.session_token, now=NOW + timedelta(seconds=2))
    assert first == second
    assert await service.validate_csrf(issued.session_token, first, now=NOW + timedelta(seconds=3))
    other = await service.issue(identity(), client_kind="browser", now=NOW)
    assert other.csrf_token != first
    assert not await service.validate_csrf(other.session_token, first, now=NOW)
    await service.revoke(issued.session_token, now=NOW + timedelta(seconds=4))
    assert not await service.validate_csrf(
        issued.session_token, first, now=NOW + timedelta(seconds=5)
    )
    with pytest.raises(SessionAuthenticationError):
        await service.get_csrf_token(issued.session_token, now=NOW + timedelta(seconds=5))
    await factory.kw["bind"].dispose()


async def test_sessions_enforce_absolute_expiry_even_when_idle_expiry_is_refreshed(
    tmp_path: Path,
) -> None:
    settings = settings_for(
        tmp_path, control_plane_session_idle_seconds=90, control_plane_session_absolute_seconds=120
    )
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ControlPlaneSessionService(factory, settings)
    issued = await service.issue(identity(), client_kind="browser", now=NOW)

    active = await service.authenticate(issued.session_token, now=NOW + timedelta(seconds=60))
    assert active.idle_expires_at == NOW + timedelta(seconds=120)
    with pytest.raises(SessionAuthenticationError, match="invalid or expired"):
        await service.authenticate(issued.session_token, now=NOW + timedelta(seconds=120))

    await factory.kw["bind"].dispose()


async def test_step_up_is_action_bound_expires_and_session_revocation_is_immediate(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    await create_schema(settings)
    factory = create_session_factory(settings)
    service = ControlPlaneSessionService(factory, settings)
    first = await service.issue(identity(), client_kind="browser", now=NOW)
    second = await service.issue(identity(), client_kind="mini_app", now=NOW)

    await service.mark_step_up(
        first.session_token, now=NOW + timedelta(seconds=10), action="credential:reveal"
    )
    authenticated = await service.authenticate(first.session_token, now=NOW + timedelta(seconds=11))
    assert has_fresh_step_up(
        authenticated,
        action="credential:reveal",
        now=NOW + timedelta(seconds=40),
        max_age_seconds=30,
    )
    assert not has_fresh_step_up(
        authenticated,
        action="credential:delete",
        now=NOW + timedelta(seconds=40),
        max_age_seconds=30,
    )
    assert not has_fresh_step_up(
        authenticated,
        action="credential:reveal",
        now=NOW + timedelta(seconds=41),
        max_age_seconds=30,
    )

    assert (
        await service.revoke_user(
            identity().telegram_user_id, now=NOW + timedelta(seconds=12), reason="bot_admin_removed"
        )
        == 2
    )
    for token in (first.session_token, second.session_token):
        with pytest.raises(SessionAuthenticationError, match="invalid or expired"):
            await service.authenticate(token, now=NOW + timedelta(seconds=13))

    await factory.kw["bind"].dispose()
