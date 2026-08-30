"""Encryption-at-rest for tenant-owned secrets (store API keys, LLM API keys) — T105.

Fernet (AES-128-CBC + HMAC, via `cryptography`) keyed by `APP_ENCRYPTION_KEY`. This is an
application-level envelope, not a KMS integration — rotating to a real KMS/HSM later only
means swapping what generates/holds the key, not the call sites (`encrypt_secret` /
`decrypt_secret` stay the same). Never log a plaintext secret; every value round-trips
through here on write and read at the repository boundary only, never earlier.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


class EncryptionKeyMissingError(RuntimeError):
    """Raised when APP_ENCRYPTION_KEY is required but not set — never a subtle fallback to
    plaintext storage."""


class SecretDecryptionError(RuntimeError):
    """Raised when a stored ciphertext can't be decrypted with the configured key (wrong
    key, corrupted value, or tampering)."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    raw = os.environ.get("APP_ENCRYPTION_KEY")
    if not raw:
        raise EncryptionKeyMissingError(
            "APP_ENCRYPTION_KEY is required to store or read tenant credentials "
            "(backend/.env.example) — generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
        )
    key = raw.encode() if isinstance(raw, str) else raw
    try:
        # Accept either a raw Fernet key (already urlsafe-base64, 44 chars) or a longer
        # arbitrary passphrase, normalized to a valid 32-byte Fernet key.
        Fernet(key)
        return Fernet(key)
    except (ValueError, base64.binascii.Error):
        import hashlib

        derived = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
        return Fernet(derived)


def encrypt_secret(plaintext: str) -> str:
    """Returns an opaque ciphertext string safe to store in `*_encrypted` columns."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "Could not decrypt stored credential — wrong APP_ENCRYPTION_KEY or corrupted value."
        ) from exc


def reset_key_cache() -> None:
    """Test-only: clears the cached Fernet instance after changing APP_ENCRYPTION_KEY."""
    _fernet.cache_clear()
