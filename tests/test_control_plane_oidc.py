from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pzp_ai_bot.config import Settings
from pzp_ai_bot.control_plane.auth.oidc import OIDCValidationError, TelegramOIDCClient
from pzp_ai_bot.control_plane.auth.transactions import IssuedOIDCTransaction

NOW = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
ISSUER = "https://oauth.telegram.org"


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'oidc.db'}",
        memory_dir=tmp_path / "memory",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
    )


def b64uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def telegram_transport(
    *, settings: Settings, claims_override: dict[str, object] | None = None
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "telegram-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": b64uint(public_numbers.n),
        "e": b64uint(public_numbers.e),
    }
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": settings.telegram_oidc_client_id,
        "sub": "telegram-subject",
        "id": "887758628",
        "first_name": "Lu",
        "last_name": "Lederrey",
        "username": "lederrey",
        "picture": "https://t.me/i/userpic/320/example.jpg",
        "nonce": "expected-nonce",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
    }
    claims.update(claims_override or {})
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "telegram-key-1"})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/auth",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                },
            )
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [jwk]})
        if request.url.path == "/token":
            assert request.headers["authorization"].startswith("Basic ")
            form = parse_qs(request.content.decode())
            assert form == {
                "grant_type": ["authorization_code"],
                "code": ["telegram-code"],
                "redirect_uri": [settings.control_plane_callback_url],
                "client_id": [settings.telegram_oidc_client_id],
                "code_verifier": ["pkce-verifier"],
            }
            return httpx.Response(
                200, json={"access_token": "not-used", "token_type": "Bearer", "id_token": token}
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


async def test_oidc_client_uses_fixed_telegram_contract_pkce_nonce_and_rs256(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    transport, seen = telegram_transport(settings=settings)
    client = TelegramOIDCClient(settings, transport=transport)
    transaction = IssuedOIDCTransaction(
        state="state",
        nonce="expected-nonce",
        code_verifier="pkce-verifier",
        code_challenge="pkce-challenge",
        code_challenge_method="S256",
        redirect_uri=settings.control_plane_callback_url,
        return_to="/",
        action="login",
    )

    authorization_url = await client.authorization_url(transaction)
    query = parse_qs(urlsplit(authorization_url).query)
    assert urlsplit(authorization_url)._replace(query="").geturl() == f"{ISSUER}/auth"
    assert query == {
        "response_type": ["code"],
        "client_id": [settings.telegram_oidc_client_id],
        "redirect_uri": [settings.control_plane_callback_url],
        "scope": ["openid profile"],
        "state": ["state"],
        "nonce": ["expected-nonce"],
        "code_challenge": ["pkce-challenge"],
        "code_challenge_method": ["S256"],
    }

    identity = await client.exchange_code(
        code="telegram-code",
        code_verifier="pkce-verifier",
        redirect_uri=settings.control_plane_callback_url,
        expected_nonce="expected-nonce",
        now=NOW,
    )

    assert identity.telegram_user_id == 887_758_628
    assert identity.username == "lederrey"
    assert identity.photo_url == "https://t.me/i/userpic/320/example.jpg"
    assert [request.url.path for request in seen] == [
        "/.well-known/openid-configuration",
        "/token",
        "/.well-known/jwks.json",
    ]
    await client.aclose()


@pytest.mark.parametrize(
    ("claims_override", "error"),
    [
        ({"nonce": "wrong"}, "nonce"),
        ({"id": "not-numeric"}, "numeric Telegram"),
        ({"iat": int((NOW - timedelta(seconds=301)).timestamp())}, "too old"),
        ({"exp": int((NOW - timedelta(seconds=31)).timestamp())}, "expired"),
    ],
)
async def test_oidc_client_rejects_invalid_identity_nonce_and_time_claims(
    tmp_path: Path, claims_override: dict[str, object], error: str
) -> None:
    settings = settings_for(tmp_path)
    transport, _ = telegram_transport(settings=settings, claims_override=claims_override)
    client = TelegramOIDCClient(settings, transport=transport)

    with pytest.raises(OIDCValidationError, match=error):
        await client.exchange_code(
            code="telegram-code",
            code_verifier="pkce-verifier",
            redirect_uri=settings.control_plane_callback_url,
            expected_nonce="expected-nonce",
            now=NOW,
        )

    await client.aclose()
