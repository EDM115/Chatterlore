from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict
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
TRUSTED_KEY_ID = "telegram-key-1"
UNKNOWN_KEY_ID = "unknown-key"
DEFAULT_RESPONSE = object()


class EndpointTransportOptions(TypedDict, total=False):
    discovery_payload: object
    token_payload: object
    jwks_payload: object
    invalid_json_path: str
    timeout_path: str


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="123:fake",  # noqa: S106
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'oidc.db'}",
        memory_dir=tmp_path / "memory",
        control_plane_enabled=True,
        control_plane_public_origin="https://control.example.test",
        telegram_oidc_client_id="123456789",
        telegram_oidc_client_secret="oidc-secret",  # noqa: S106
        control_plane_master_keys='{"test":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        control_plane_active_master_key_id="test",
    )


def b64uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def telegram_transport(
    *,
    settings: Settings,
    claims_override: dict[str, object] | None = None,
    remove_claims: tuple[str, ...] = (),
    algorithm: str = "RS256",
    key_id: str | None = TRUSTED_KEY_ID,
    wrong_signing_key: bool = False,
    discovery_payload: object = DEFAULT_RESPONSE,
    token_payload: object = DEFAULT_RESPONSE,
    jwks_payload: object = DEFAULT_RESPONSE,
    invalid_json_path: str | None = None,
    timeout_path: str | None = None,
    single_use_code: bool = False,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": TRUSTED_KEY_ID,
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
        "name": "Lu Lederrey",
        "given_name": "Lu",
        "family_name": "Lederrey",
        "preferred_username": "lederrey",
        "picture": "https://t.me/i/userpic/320/example.jpg",
        "nonce": "expected-nonce",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
    }
    claims.update(claims_override or {})
    for claim in remove_claims:
        claims.pop(claim, None)
    signing_key: object = private_key
    if wrong_signing_key:
        signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if algorithm == "HS256":
        signing_key = "untrusted-hmac-key-with-at-least-32-bytes"
    token_headers = {} if key_id is None else {"kid": key_id}
    token = jwt.encode(claims, signing_key, algorithm=algorithm, headers=token_headers)
    seen: list[httpx.Request] = []
    token_exchanges = 0

    default_discovery = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/auth",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_exchanges
        seen.append(request)
        if request.url.path == timeout_path:
            raise httpx.ReadTimeout("simulated Telegram timeout", request=request)
        if request.url.path == invalid_json_path:
            return httpx.Response(200, content=b"{")
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json=default_discovery
                if discovery_payload is DEFAULT_RESPONSE
                else discovery_payload,
            )
        if request.url.path == "/.well-known/jwks.json":
            return httpx.Response(
                200, json={"keys": [jwk]} if jwks_payload is DEFAULT_RESPONSE else jwks_payload
            )
        if request.url.path == "/token":
            assert request.headers["authorization"].startswith("Basic ")
            form = parse_qs(request.content.decode())
            if form != {
                "grant_type": ["authorization_code"],
                "code": ["telegram-code"],
                "redirect_uri": [settings.control_plane_callback_url],
                "client_id": [settings.telegram_oidc_client_id],
                "code_verifier": ["pkce-verifier"],
            }:
                return httpx.Response(400, json={"error": "invalid_grant"})
            if single_use_code and token_exchanges:
                return httpx.Response(400, json={"error": "invalid_grant"})
            token_exchanges += 1
            return httpx.Response(
                200,
                json={"access_token": "not-used", "token_type": "Bearer", "id_token": token}
                if token_payload is DEFAULT_RESPONSE
                else token_payload,
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
    assert identity.first_name == "Lu"
    assert identity.last_name == "Lederrey"
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
        ({"iat": int((NOW + timedelta(seconds=31)).timestamp())}, "future"),
        ({"exp": int((NOW - timedelta(seconds=31)).timestamp())}, "expired"),
        ({"nbf": int((NOW + timedelta(seconds=31)).timestamp())}, "not active"),
        ({"iss": "https://evil.example.test"}, "validation failed"),
        ({"aud": "wrong-client"}, "validation failed"),
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


@pytest.mark.parametrize(
    ("algorithm", "key_id", "wrong_signing_key", "error"),
    [
        ("HS256", TRUSTED_KEY_ID, False, "must use RS256"),
        ("RS256", None, False, "key ID is missing"),
        ("RS256", UNKNOWN_KEY_ID, False, "signing key is unknown"),
        ("RS256", TRUSTED_KEY_ID, True, "validation failed"),
    ],
)
async def test_oidc_client_rejects_wrong_algorithm_key_id_and_signature(
    tmp_path: Path, algorithm: str, key_id: str | None, wrong_signing_key: bool, error: str
) -> None:
    settings = settings_for(tmp_path)
    transport, seen = telegram_transport(
        settings=settings, algorithm=algorithm, key_id=key_id, wrong_signing_key=wrong_signing_key
    )
    client = TelegramOIDCClient(settings, transport=transport)

    with pytest.raises(OIDCValidationError, match=error):
        await client.exchange_code(
            code="telegram-code",
            code_verifier="pkce-verifier",
            redirect_uri=settings.control_plane_callback_url,
            expected_nonce="expected-nonce",
            now=NOW,
        )

    if key_id == UNKNOWN_KEY_ID:
        assert [request.url.path for request in seen].count("/.well-known/jwks.json") == 2
    await client.aclose()


@pytest.mark.parametrize(
    ("transport_options", "error"),
    [
        ({"invalid_json_path": "/.well-known/openid-configuration"}, "invalid JSON"),
        ({"discovery_payload": []}, "invalid payload"),
        ({"discovery_payload": {"issuer": ISSUER}}, "unexpected authorization_endpoint"),
        ({"invalid_json_path": "/token"}, "invalid JSON"),
        ({"token_payload": []}, "invalid payload"),
        ({"token_payload": {}}, "missing an ID token"),
        ({"invalid_json_path": "/.well-known/jwks.json"}, "invalid JSON"),
        ({"jwks_payload": []}, "invalid payload"),
        ({"jwks_payload": {}}, "JWKS payload is invalid"),
        ({"timeout_path": "/token"}, "request failed"),
    ],
)
async def test_oidc_client_fails_closed_for_malformed_or_unavailable_endpoints(
    tmp_path: Path, transport_options: EndpointTransportOptions, error: str
) -> None:
    settings = settings_for(tmp_path)
    transport, _ = telegram_transport(settings=settings, **transport_options)
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


async def test_oidc_client_fails_closed_for_wrong_pkce_code_replay_and_nonce_replay(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    transport, _ = telegram_transport(settings=settings, single_use_code=True)
    client = TelegramOIDCClient(settings, transport=transport)

    with pytest.raises(OIDCValidationError, match="request failed"):
        await client.exchange_code(
            code="telegram-code",
            code_verifier="wrong-verifier",
            redirect_uri=settings.control_plane_callback_url,
            expected_nonce="expected-nonce",
            now=NOW,
        )
    await client.exchange_code(
        code="telegram-code",
        code_verifier="pkce-verifier",
        redirect_uri=settings.control_plane_callback_url,
        expected_nonce="expected-nonce",
        now=NOW,
    )
    with pytest.raises(OIDCValidationError, match="request failed"):
        await client.exchange_code(
            code="telegram-code",
            code_verifier="pkce-verifier",
            redirect_uri=settings.control_plane_callback_url,
            expected_nonce="expected-nonce",
            now=NOW,
        )

    fresh_transport, _ = telegram_transport(settings=settings)
    fresh_client = TelegramOIDCClient(settings, transport=fresh_transport)
    with pytest.raises(OIDCValidationError, match="nonce"):
        await fresh_client.exchange_code(
            code="telegram-code",
            code_verifier="pkce-verifier",
            redirect_uri=settings.control_plane_callback_url,
            expected_nonce="different-transaction-nonce",
            now=NOW,
        )

    await client.aclose()
    await fresh_client.aclose()
