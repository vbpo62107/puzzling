from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("security.encryption")

_TOKEN_ENV = "TOKEN_ENCRYPTION_KEY"
_MAGIC_PREFIX = b"pzl-token::v1::"


class EncryptionError(Exception):
    """Base class for encryption related failures."""


class MissingTokenEncryptionKeyError(EncryptionError):
    """Raised when encrypted content is encountered without a configured key."""


class TokenDecryptionError(EncryptionError):
    """Raised when encrypted content cannot be decrypted with the configured key."""


_cipher_lock = threading.Lock()
_cached_cipher: Optional[Fernet] = None
_cached_key_material: Optional[str] = None
_warned_missing_key = False


def _derive_key_material(raw_key: str) -> bytes:
    raw_bytes = raw_key.encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(raw_bytes)
        if len(decoded) == 32:
            return base64.urlsafe_b64encode(decoded)
    except Exception:
        pass
    if len(raw_bytes) == 32:
        return base64.urlsafe_b64encode(raw_bytes)
    digest = hashlib.sha256(raw_bytes).digest()
    return base64.urlsafe_b64encode(digest)


def _build_cipher() -> Optional[Fernet]:
    key = os.getenv(_TOKEN_ENV)
    if not key:
        return None
    material = _derive_key_material(key.strip())
    return Fernet(material)


def _get_cipher() -> Optional[Fernet]:
    global _cached_cipher, _cached_key_material
    key_material = os.getenv(_TOKEN_ENV)
    with _cipher_lock:
        if key_material != _cached_key_material:
            _cached_key_material = key_material
            _cached_cipher = _build_cipher() if key_material else None
    return _cached_cipher


def is_encryption_enabled() -> bool:
    return _get_cipher() is not None


def is_encrypted(data: bytes) -> bool:
    return data.startswith(_MAGIC_PREFIX)


def encrypt(data: bytes) -> bytes:
    global _warned_missing_key
    cipher = _get_cipher()
    if cipher is None:
        if not _warned_missing_key:
            logger.warning(
                "TOKEN_ENCRYPTION_KEY is not configured; storing credentials in plaintext."
            )
            _warned_missing_key = True
        return data
    token = cipher.encrypt(data)
    return _MAGIC_PREFIX + token


def decrypt(data: bytes) -> bytes:
    if not is_encrypted(data):
        return data

    cipher = _get_cipher()
    if cipher is None:
        raise MissingTokenEncryptionKeyError(
            "Encrypted token detected but TOKEN_ENCRYPTION_KEY is not configured."
        )

    payload = data[len(_MAGIC_PREFIX) :]
    try:
        return cipher.decrypt(payload)
    except InvalidToken as exc:  # pragma: no cover - validated via tests
        raise TokenDecryptionError("Failed to decrypt token payload") from exc


def reset_encryption_state_for_tests() -> None:
    global _cached_cipher, _cached_key_material, _warned_missing_key
    with _cipher_lock:
        _cached_cipher = None
        _cached_key_material = None
    _warned_missing_key = False


__all__ = [
    "EncryptionError",
    "MissingTokenEncryptionKeyError",
    "TokenDecryptionError",
    "encrypt",
    "decrypt",
    "is_encrypted",
    "is_encryption_enabled",
    "reset_encryption_state_for_tests",
]
