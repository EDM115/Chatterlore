from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pzp_ai_bot.config import Settings
from pzp_ai_bot.db.repos.control_plane import OIDCTransactionRepository
from pzp_ai_bot.db.session import session_scope


class OIDCTransactionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedOIDCTransaction:
    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    code_challenge_method: str
    redirect_uri: str
    return_to: str
    action: str


@dataclass(frozen=True, slots=True)
class ConsumedOIDCTransaction:
    nonce: str
    code_verifier: str
    redirect_uri: str
    return_to: str
    action: str
    expected_telegram_user_id: int | None


class OIDCTransactionService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._protector = _OIDCTransactionProtector(settings.telegram_oidc_client_secret)

    async def create(
        self,
        *,
        return_to: str,
        now: datetime,
        action: str = "login",
        expected_telegram_user_id: int | None = None,
    ) -> IssuedOIDCTransaction:
        safe_return = validate_relative_return_to(return_to)
        if action not in {"login", "step_up"}:
            raise OIDCTransactionError("OIDC transaction action is invalid")
        state = secrets.token_urlsafe(32)
        state_hash = _secret_hash(state)
        oidc_nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        redirect_uri = self.settings.control_plane_callback_url
        pkce_nonce, pkce_ciphertext = self._protector.encrypt(
            code_verifier, state_hash=state_hash, redirect_uri=redirect_uri
        )
        async with session_scope(self.session_factory) as session:
            await OIDCTransactionRepository(session).add(
                state_hash=state_hash,
                pkce_ciphertext=pkce_ciphertext,
                pkce_nonce=pkce_nonce,
                oidc_nonce=oidc_nonce,
                redirect_uri=redirect_uri,
                return_to=safe_return,
                action=action,
                expected_telegram_user_id=expected_telegram_user_id,
                created_at=now,
                expires_at=now
                + timedelta(seconds=self.settings.control_plane_oidc_transaction_ttl_seconds),
            )
        challenge = _base64url(hashlib.sha256(code_verifier.encode()).digest())
        return IssuedOIDCTransaction(
            state=state,
            nonce=oidc_nonce,
            code_verifier=code_verifier,
            code_challenge=challenge,
            code_challenge_method="S256",
            redirect_uri=redirect_uri,
            return_to=safe_return,
            action=action,
        )

    async def consume(self, raw_state: str, *, now: datetime) -> ConsumedOIDCTransaction:
        if not raw_state:
            raise OIDCTransactionError("OIDC transaction is invalid, expired, or already used")
        state_hash = _secret_hash(raw_state)
        async with session_scope(self.session_factory) as session:
            stored = await OIDCTransactionRepository(session).consume(
                state_hash=state_hash, consumed_at=now
            )
            if stored is None:
                raise OIDCTransactionError("OIDC transaction is invalid, expired, or already used")
            try:
                verifier = self._protector.decrypt(
                    stored.pkce_nonce,
                    stored.pkce_ciphertext,
                    state_hash=state_hash,
                    redirect_uri=stored.redirect_uri,
                )
            except InvalidTag as exc:
                raise OIDCTransactionError("OIDC transaction cannot be decrypted") from exc
            return ConsumedOIDCTransaction(
                nonce=stored.oidc_nonce,
                code_verifier=verifier,
                redirect_uri=stored.redirect_uri,
                return_to=stored.return_to,
                action=stored.action,
                expected_telegram_user_id=stored.expected_telegram_user_id,
            )


def validate_relative_return_to(value: str) -> str:
    if (
        not value
        or len(value) > 512
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise OIDCTransactionError("return_to must be a safe relative path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise OIDCTransactionError("return_to must be a safe relative path")
    return value


class _OIDCTransactionProtector:
    def __init__(self, bootstrap_secret: str) -> None:
        if not bootstrap_secret:
            raise OIDCTransactionError("OIDC transaction encryption is not configured")
        self._key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"pzp-ai-control-plane-oidc-v1",
            info=b"oidc-pkce-transaction",
        ).derive(bootstrap_secret.encode())

    def encrypt(self, verifier: str, *, state_hash: str, redirect_uri: str) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key).encrypt(
            nonce,
            verifier.encode(),
            _transaction_aad(state_hash=state_hash, redirect_uri=redirect_uri),
        )
        return nonce, ciphertext

    def decrypt(
        self, nonce: bytes, ciphertext: bytes, *, state_hash: str, redirect_uri: str
    ) -> str:
        plaintext = AESGCM(self._key).decrypt(
            nonce, ciphertext, _transaction_aad(state_hash=state_hash, redirect_uri=redirect_uri)
        )
        return plaintext.decode()


def _transaction_aad(*, state_hash: str, redirect_uri: str) -> bytes:
    return f"{state_hash}\0{redirect_uri}".encode()


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
