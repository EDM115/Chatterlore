from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from chatterlore.config import Settings

from .identity import TelegramIdentity
from .transactions import IssuedOIDCTransaction

TELEGRAM_OIDC_ISSUER = "https://oauth.telegram.org"
TELEGRAM_OIDC_DISCOVERY_URL = "https://oauth.telegram.org/.well-known/openid-configuration"
TELEGRAM_OIDC_AUTHORIZATION_URL = "https://oauth.telegram.org/auth"
TELEGRAM_OIDC_TOKEN_URL = "https://oauth.telegram.org/token"  # noqa: S105
TELEGRAM_OIDC_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"


class OIDCValidationError(ValueError):
    pass


class TelegramOIDCClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False
        )
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def authorization_url(self, transaction: IssuedOIDCTransaction) -> str:
        discovery = await self._get_discovery()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.telegram_oidc_client_id,
                "redirect_uri": transaction.redirect_uri,
                "scope": "openid profile",
                "state": transaction.state,
                "nonce": transaction.nonce,
                "code_challenge": transaction.code_challenge,
                "code_challenge_method": transaction.code_challenge_method,
            }
        )
        return f"{discovery['authorization_endpoint']}?{query}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
        now: datetime,
    ) -> TelegramIdentity:
        if not code:
            raise OIDCValidationError("OIDC authorization code is missing")
        discovery = await self._get_discovery()
        response = await self._request(
            "POST",
            discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.settings.telegram_oidc_client_id,
                "code_verifier": code_verifier,
            },
            auth=httpx.BasicAuth(
                self.settings.telegram_oidc_client_id, self.settings.telegram_oidc_client_secret
            ),
        )
        payload = _json_object(response)
        id_token = payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OIDCValidationError("OIDC token response is missing an ID token")
        claims = await self._decode_id_token(id_token)
        return _identity_from_claims(
            claims,
            expected_nonce=expected_nonce,
            now=now,
            max_age_seconds=self.settings.control_plane_auth_max_age_seconds,
        )

    async def _get_discovery(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        response = await self._request("GET", TELEGRAM_OIDC_DISCOVERY_URL)
        discovery = _json_object(response)
        expected = {
            "issuer": TELEGRAM_OIDC_ISSUER,
            "authorization_endpoint": TELEGRAM_OIDC_AUTHORIZATION_URL,
            "token_endpoint": TELEGRAM_OIDC_TOKEN_URL,
            "jwks_uri": TELEGRAM_OIDC_JWKS_URL,
        }
        for key, value in expected.items():
            if discovery.get(key) != value:
                raise OIDCValidationError(f"Telegram OIDC discovery has an unexpected {key}")
        self._discovery = discovery
        return discovery

    async def _decode_id_token(self, encoded: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(encoded)
        except jwt.InvalidTokenError as exc:
            raise OIDCValidationError("OIDC ID token header is invalid") from exc
        if header.get("alg") != "RS256":
            raise OIDCValidationError("OIDC ID token must use RS256")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise OIDCValidationError("OIDC ID token key ID is missing")
        jwk = await self._find_jwk(kid)
        try:
            public_key = jwt.PyJWK.from_dict(jwk, algorithm="RS256").key
            claims = jwt.decode(
                encoded,
                key=public_key,
                algorithms=["RS256"],
                audience=self.settings.telegram_oidc_client_id,
                issuer=TELEGRAM_OIDC_ISSUER,
                options={
                    "require": ["iss", "aud", "sub", "id", "nonce", "iat", "exp"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
            raise OIDCValidationError("OIDC ID token validation failed") from exc
        if not isinstance(claims, dict):
            raise OIDCValidationError("OIDC ID token claims are invalid")
        return claims

    async def _find_jwk(self, kid: str) -> dict[str, Any]:
        for refresh in (False, True):
            keys = await self._get_jwks(refresh=refresh)
            for candidate in keys:
                if (
                    isinstance(candidate, dict)
                    and candidate.get("kid") == kid
                    and candidate.get("kty") == "RSA"
                    and candidate.get("alg") in {None, "RS256"}
                    and candidate.get("use") in {None, "sig"}
                ):
                    return candidate
        raise OIDCValidationError("OIDC signing key is unknown")

    async def _get_jwks(self, *, refresh: bool) -> list[Any]:
        if self._jwks is None or refresh:
            discovery = await self._get_discovery()
            response = await self._request("GET", discovery["jwks_uri"])
            self._jwks = _json_object(response)
        keys = self._jwks.get("keys")
        if not isinstance(keys, list):
            raise OIDCValidationError("OIDC JWKS payload is invalid")
        return keys

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            raise OIDCValidationError("Telegram OIDC request failed") from exc


def _identity_from_claims(
    claims: dict[str, Any],
    *,
    expected_nonce: str,
    now: datetime,
    max_age_seconds: int,
    clock_skew_seconds: int = 30,
) -> TelegramIdentity:
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not hmac.compare_digest(nonce, expected_nonce):
        raise OIDCValidationError("OIDC nonce does not match")
    utc_now = _as_utc(now)
    issued_at = _numeric_date(claims, "iat")
    expires_at = _numeric_date(claims, "exp")
    if issued_at > utc_now.timestamp() + clock_skew_seconds:
        raise OIDCValidationError("OIDC ID token was issued in the future")
    if utc_now.timestamp() - issued_at > max_age_seconds:
        raise OIDCValidationError("OIDC authentication is too old")
    if expires_at <= utc_now.timestamp() - clock_skew_seconds:
        raise OIDCValidationError("OIDC ID token has expired")
    not_before = claims.get("nbf")
    if not_before is not None:
        if isinstance(not_before, bool) or not isinstance(not_before, (int, float)):
            raise OIDCValidationError("OIDC nbf claim is invalid")
        if float(not_before) > utc_now.timestamp() + clock_skew_seconds:
            raise OIDCValidationError("OIDC ID token is not active")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise OIDCValidationError("OIDC subject is invalid")
    telegram_user_id = _telegram_user_id(claims.get("id"))
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        first_name=_required_string(claims, "given_name", maximum=255),
        last_name=_optional_string(claims, "family_name", maximum=255),
        username=_optional_string(claims, "preferred_username", maximum=255),
        language_code=None,
        photo_url=_optional_string(claims, "picture", maximum=512),
        is_bot=False,
        is_premium=None,
        authenticated_at=datetime.fromtimestamp(issued_at, tz=UTC),
    )


def _telegram_user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise OIDCValidationError("OIDC requires a numeric Telegram identity")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.isdecimal():
        result = int(value)
    else:
        raise OIDCValidationError("OIDC requires a numeric Telegram identity")
    if result < 1 or result > 9_223_372_036_854_775_807:
        raise OIDCValidationError("OIDC requires a numeric Telegram identity")
    return result


def _numeric_date(claims: dict[str, Any], key: str) -> float:
    value = claims.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OIDCValidationError(f"OIDC {key} claim is invalid")
    return float(value)


def _required_string(payload: dict[str, Any], key: str, *, maximum: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise OIDCValidationError(f"OIDC {key} claim is invalid")
    return value


def _optional_string(payload: dict[str, Any], key: str, *, maximum: int) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise OIDCValidationError(f"OIDC {key} claim is invalid")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise OIDCValidationError("OIDC validation time must be timezone-aware")
    return value.astimezone(UTC)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise OIDCValidationError("Telegram OIDC returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise OIDCValidationError("Telegram OIDC returned an invalid payload")
    return payload
