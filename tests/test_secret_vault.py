from __future__ import annotations

import base64
import importlib
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from pzp_ai_bot.config import Settings


def encoded_key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode()


def enabled_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123:fake",
        "bot_admins": "111",
        "db_url": f"sqlite+aiosqlite:///{tmp_path / 'vault.db'}",
        "memory_dir": tmp_path / "memory",
        "control_plane_enabled": True,
        "control_plane_public_origin": "https://control.example.test",
        "telegram_oidc_client_id": "123456789",
        "telegram_oidc_client_secret": "oidc-secret",
        "control_plane_master_keys": f'{{"primary":"{encoded_key(1)}"}}',
        "control_plane_active_master_key_id": "primary",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def vault_api():
    try:
        module = importlib.import_module("pzp_ai_bot.control_plane.secrets.crypto")
    except ModuleNotFoundError:
        pytest.fail("provider secret vault is not implemented")
    return (
        module.EncryptedProviderSecret,
        module.MasterKeyring,
        module.ProviderSecretVault,
        module.SecretVaultError,
    )


def test_enabled_control_plane_requires_a_valid_active_master_key(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="master key"):
        enabled_settings(
            tmp_path, control_plane_master_keys="", control_plane_active_master_key_id=""
        )
    with pytest.raises(ValidationError, match="base64"):
        enabled_settings(tmp_path, control_plane_master_keys='{"primary":"not-base64"}')
    with pytest.raises(ValidationError, match="32 bytes"):
        enabled_settings(
            tmp_path,
            control_plane_master_keys=(f'{{"primary":"{base64.b64encode(bytes(31)).decode()}"}}'),
        )
    with pytest.raises(ValidationError, match="active"):
        enabled_settings(tmp_path, control_plane_active_master_key_id="missing")

    settings = enabled_settings(
        tmp_path,
        control_plane_master_keys=(f'{{"old":"{encoded_key(1)}","primary":"{encoded_key(2)}"}}'),
    )

    assert settings.control_plane_master_key_bytes == {
        "old": bytes([1]) * 32,
        "primary": bytes([2]) * 32,
    }
    assert settings.control_plane_master_key_ids == ("old", "primary")
    rendered = repr(settings.control_plane_safe_config)
    assert encoded_key(1) not in rendered
    assert encoded_key(2) not in rendered


def test_envelope_encryption_round_trips_and_binds_every_identity_field() -> None:
    EncryptedProviderSecret, MasterKeyring, ProviderSecretVault, SecretVaultError = (  # noqa: N806
        vault_api()
    )
    keyring = MasterKeyring(
        keys={"old": bytes([1]) * 32, "primary": bytes([2]) * 32}, active_key_id="old"
    )
    vault = ProviderSecretVault(keyring)
    secret = "sk-超-secret-" + ("x" * 500)

    encrypted = vault.encrypt(secret, profile_name="primary-profile", credential_id=42)

    assert isinstance(encrypted, EncryptedProviderSecret)
    assert vault.decrypt(encrypted, profile_name="primary-profile", credential_id=42) == secret
    assert secret.encode() not in encrypted.ciphertext
    assert len(encrypted.secret_nonce) == 12
    assert len(encrypted.wrap_nonce) == 12
    assert encrypted.last_four == "xxxx"
    assert vault.matches_fingerprint(secret, encrypted.fingerprint, key_id="old")
    assert not vault.matches_fingerprint(secret + "!", encrypted.fingerprint, key_id="old")

    tampered_values = [
        replace(encrypted, ciphertext=_flip(encrypted.ciphertext)),
        replace(encrypted, secret_nonce=_flip(encrypted.secret_nonce)),
        replace(encrypted, wrapped_dek=_flip(encrypted.wrapped_dek)),
        replace(encrypted, wrap_nonce=_flip(encrypted.wrap_nonce)),
        replace(encrypted, master_key_id="missing"),
    ]
    for tampered in tampered_values:
        with pytest.raises(SecretVaultError, match="credential cannot be decrypted") as caught:
            vault.decrypt(tampered, profile_name="primary-profile", credential_id=42)
        assert secret not in str(caught.value)

    for profile_name, credential_id in [("other-profile", 42), ("primary-profile", 43)]:
        with pytest.raises(SecretVaultError, match="credential cannot be decrypted"):
            vault.decrypt(encrypted, profile_name=profile_name, credential_id=credential_id)


def test_rewrap_preserves_secret_ciphertext_and_verifies_the_new_wrapping() -> None:
    _, MasterKeyring, ProviderSecretVault, _ = vault_api()  # noqa: N806
    old_key = bytes([1]) * 32
    new_key = bytes([2]) * 32
    vault = ProviderSecretVault(
        MasterKeyring(keys={"old": old_key, "new": new_key}, active_key_id="old")
    )
    encrypted = vault.encrypt("sk-rewrap-value", profile_name="profile", credential_id=7)

    rewrapped = vault.rewrap(
        encrypted, profile_name="profile", credential_id=7, target_key_id="new"
    )

    assert rewrapped.master_key_id == "new"
    assert rewrapped.ciphertext == encrypted.ciphertext
    assert rewrapped.secret_nonce == encrypted.secret_nonce
    assert rewrapped.fingerprint == encrypted.fingerprint
    assert rewrapped.last_four == encrypted.last_four
    assert rewrapped.wrapped_dek != encrypted.wrapped_dek
    assert rewrapped.wrap_nonce != encrypted.wrap_nonce
    new_only_vault = ProviderSecretVault(MasterKeyring(keys={"new": new_key}, active_key_id="new"))
    assert (
        new_only_vault.decrypt(rewrapped, profile_name="profile", credential_id=7)
        == "sk-rewrap-value"
    )


def _flip(value: bytes) -> bytes:
    return bytes([value[0] ^ 1]) + value[1:]
