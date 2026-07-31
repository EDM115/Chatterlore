from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_HKDF_SALT = b"pzp-ai-control-plane-provider-vault-v1"
_WRAP_INFO = b"provider-credential-dek-wrap"
_FINGERPRINT_INFO = b"provider-credential-fingerprint"
_SECRET_AAD_PREFIX = b"pzp-ai/provider-secret/v1"
_WRAP_AAD_PREFIX = b"pzp-ai/provider-dek/v1"


class SecretVaultError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedProviderSecret:
    ciphertext: bytes
    secret_nonce: bytes
    wrapped_dek: bytes
    wrap_nonce: bytes
    master_key_id: str
    fingerprint: str
    last_four: str


@dataclass(frozen=True, slots=True)
class MasterKeyring:
    keys: dict[str, bytes]
    active_key_id: str

    def __post_init__(self) -> None:
        if not self.keys or self.active_key_id not in self.keys:
            raise SecretVaultError("provider credential encryption is not configured")
        if any(not key_id or len(material) != 32 for key_id, material in self.keys.items()):
            raise SecretVaultError("provider credential encryption is not configured")

    def key(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError as exc:
            raise SecretVaultError("provider credential cannot be decrypted") from exc


class ProviderSecretVault:
    def __init__(self, keyring: MasterKeyring) -> None:
        self.keyring = keyring

    def encrypt(
        self, value: str, *, profile_name: str, credential_id: int
    ) -> EncryptedProviderSecret:
        plaintext = value.encode()
        master_key_id = self.keyring.active_key_id
        master_key = self.keyring.key(master_key_id)
        fingerprint = self.fingerprint(value, key_id=master_key_id)
        last_four = value[-4:]
        dek = AESGCM.generate_key(bit_length=256)
        secret_nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(dek).encrypt(
            secret_nonce,
            plaintext,
            _secret_aad(
                profile_name=profile_name,
                credential_id=credential_id,
                fingerprint=fingerprint,
                last_four=last_four,
            ),
        )
        wrap_nonce = secrets.token_bytes(12)
        wrapped_dek = AESGCM(_derive(master_key, _WRAP_INFO)).encrypt(
            wrap_nonce,
            dek,
            _wrap_aad(
                profile_name=profile_name, credential_id=credential_id, master_key_id=master_key_id
            ),
        )
        return EncryptedProviderSecret(
            ciphertext=ciphertext,
            secret_nonce=secret_nonce,
            wrapped_dek=wrapped_dek,
            wrap_nonce=wrap_nonce,
            master_key_id=master_key_id,
            fingerprint=fingerprint,
            last_four=last_four,
        )

    def decrypt(
        self, encrypted: EncryptedProviderSecret, *, profile_name: str, credential_id: int
    ) -> str:
        try:
            dek = self._unwrap_dek(
                encrypted, profile_name=profile_name, credential_id=credential_id
            )
            plaintext = AESGCM(dek).decrypt(
                encrypted.secret_nonce,
                encrypted.ciphertext,
                _secret_aad(
                    profile_name=profile_name,
                    credential_id=credential_id,
                    fingerprint=encrypted.fingerprint,
                    last_four=encrypted.last_four,
                ),
            )
            return plaintext.decode()
        except (InvalidTag, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise SecretVaultError("provider credential cannot be decrypted") from exc

    def fingerprint(self, value: str, *, key_id: str) -> str:
        key = _derive(self.keyring.key(key_id), _FINGERPRINT_INFO)
        return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()

    def matches_fingerprint(self, value: str, expected: str, *, key_id: str) -> bool:
        return hmac.compare_digest(self.fingerprint(value, key_id=key_id), expected)

    def rewrap(
        self,
        encrypted: EncryptedProviderSecret,
        *,
        profile_name: str,
        credential_id: int,
        target_key_id: str,
    ) -> EncryptedProviderSecret:
        try:
            dek = self._unwrap_dek(
                encrypted, profile_name=profile_name, credential_id=credential_id
            )
            target_key = self.keyring.key(target_key_id)
            wrap_nonce = secrets.token_bytes(12)
            wrapped_dek = AESGCM(_derive(target_key, _WRAP_INFO)).encrypt(
                wrap_nonce,
                dek,
                _wrap_aad(
                    profile_name=profile_name,
                    credential_id=credential_id,
                    master_key_id=target_key_id,
                ),
            )
            rewrapped = replace(
                encrypted,
                wrapped_dek=wrapped_dek,
                wrap_nonce=wrap_nonce,
                master_key_id=target_key_id,
            )
            verified = self._unwrap_dek(
                rewrapped, profile_name=profile_name, credential_id=credential_id
            )
            if not hmac.compare_digest(dek, verified):
                raise SecretVaultError("provider credential cannot be rewrapped")
            self.decrypt(rewrapped, profile_name=profile_name, credential_id=credential_id)
            return rewrapped
        except SecretVaultError:
            raise
        except (InvalidTag, ValueError, TypeError) as exc:
            raise SecretVaultError("provider credential cannot be rewrapped") from exc

    def _unwrap_dek(
        self, encrypted: EncryptedProviderSecret, *, profile_name: str, credential_id: int
    ) -> bytes:
        master_key = self.keyring.key(encrypted.master_key_id)
        return AESGCM(_derive(master_key, _WRAP_INFO)).decrypt(
            encrypted.wrap_nonce,
            encrypted.wrapped_dek,
            _wrap_aad(
                profile_name=profile_name,
                credential_id=credential_id,
                master_key_id=encrypted.master_key_id,
            ),
        )


def _derive(master_key: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_HKDF_SALT, info=info).derive(master_key)


def _secret_aad(
    *, profile_name: str, credential_id: int, fingerprint: str, last_four: str
) -> bytes:
    return b"\0".join(
        (
            _SECRET_AAD_PREFIX,
            profile_name.encode(),
            str(credential_id).encode(),
            fingerprint.encode(),
            last_four.encode(),
        )
    )


def _wrap_aad(*, profile_name: str, credential_id: int, master_key_id: str) -> bytes:
    return b"\0".join(
        (
            _WRAP_AAD_PREFIX,
            profile_name.encode(),
            str(credential_id).encode(),
            master_key_id.encode(),
        )
    )
