from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional

import pytest

from security import token_store as token_store_module
from security.encryption import (
    MissingTokenEncryptionKeyError,
    decrypt,
    is_encrypted,
    reset_encryption_state_for_tests,
)
from security.logging_utils import mask_user_id
from security.token_store import TokenState, TokenStore


class DummyGoogleAuth:
    def __init__(self, payload: Optional[Dict[str, object]] = None) -> None:
        self._payload = payload or {"access_token": "token", "refresh_token": "refresh"}
        self.credentials = SimpleNamespace(invalid=False)
        self.access_token_expired = bool(self._payload.get("expired", False))
        self.settings: Dict[str, object] = {}

    def SaveCredentialsFile(self, filename: str) -> None:
        Path(filename).write_text(json.dumps(self._payload))

    def LoadCredentialsFile(self, filename: str) -> None:
        data = json.loads(Path(filename).read_text())
        self._payload = data
        self.credentials = SimpleNamespace(invalid=bool(data.get("invalid", False)))
        self.access_token_expired = bool(data.get("expired", False))

    def Refresh(self) -> None:  # pragma: no cover - tests do not rely on refresh
        return

    def Authorize(self) -> None:  # pragma: no cover - not used in these tests
        return


@pytest.fixture(autouse=True)
def _reset_encryption_state():
    reset_encryption_state_for_tests()
    yield
    reset_encryption_state_for_tests()


@pytest.fixture
def store(tmp_path, monkeypatch) -> TokenStore:
    monkeypatch.setattr(token_store_module, "GoogleAuth", DummyGoogleAuth)
    monkeypatch.setattr(
        TokenStore,
        "configure_gauth",
        lambda self, gauth, token_path: gauth,
        raising=False,
    )
    monkeypatch.setattr(
        token_store_module,
        "get_user_token_path",
        lambda user_id: tmp_path / f"token_{user_id}.json",
    )
    return TokenStore(base_dir=tmp_path)


def test_atomic_save_encrypts_with_key(store, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "super-secret")
    reset_encryption_state_for_tests()
    gauth = DummyGoogleAuth()
    token_path = store.get_token_path(1)

    store._atomic_save(1, gauth, token_path)

    payload = token_path.read_bytes()
    assert is_encrypted(payload)

    result = store._load(1)
    assert result.state is TokenState.VALID
    assert isinstance(result.gauth, DummyGoogleAuth)


def test_atomic_save_plaintext_without_key_warns(store, monkeypatch, caplog):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    reset_encryption_state_for_tests()
    caplog.set_level(logging.WARNING, logger="security.encryption")

    token_path = store.get_token_path(2)
    store._atomic_save(2, DummyGoogleAuth(), token_path)

    payload = token_path.read_bytes()
    assert not is_encrypted(payload)
    assert any("TOKEN_ENCRYPTION_KEY is not configured" in rec.getMessage() for rec in caplog.records)


def test_load_encrypted_without_key_quarantines(store, monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "initial-key")
    reset_encryption_state_for_tests()
    token_path = store.get_token_path(3)
    store._atomic_save(3, DummyGoogleAuth(), token_path)

    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    reset_encryption_state_for_tests()

    caplog.set_level(logging.ERROR)
    with pytest.raises(MissingTokenEncryptionKeyError):
        store._load(3)

    quarantine_dir = store._base_dir / "quarantine"
    quarantined_files = list(quarantine_dir.glob("token_3_*.json"))
    assert quarantined_files, "Encrypted token should be moved to quarantine"

    messages = [record.getMessage() for record in caplog.records if record.name == "auth"]
    assert messages, "Expected auth logger to emit an error"
    for message in messages:
        assert "user 3" not in message
        assert token_path.name not in message

    record = next(record for record in caplog.records if record.name == "auth")
    assert getattr(record, "masked_user_id", None) == mask_user_id(3)
    assert getattr(record, "token_event", None) == "missing_key"


def test_load_with_wrong_key_returns_corrupted(store, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "first-key")
    reset_encryption_state_for_tests()
    token_path = store.get_token_path(4)
    store._atomic_save(4, DummyGoogleAuth({"access_token": "first"}), token_path)

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "other-key")
    reset_encryption_state_for_tests()

    result = store._load(4)
    assert result.state is TokenState.CORRUPTED
    assert result.error == "decrypt_failed"
    assert result.quarantined_to is not None


def test_atomic_save_is_thread_safe(store, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "thread-key")
    reset_encryption_state_for_tests()
    token_path = store.get_token_path(5)

    payloads = [
        {"access_token": "first", "sequence": 1},
        {"access_token": "second", "sequence": 2},
    ]

    def writer(payload: Dict[str, object]) -> None:
        store._atomic_save(5, DummyGoogleAuth(payload), token_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        for payload in payloads:
            executor.submit(writer, payload)

    data = json.loads(decrypt(token_path.read_bytes()).decode("utf-8"))
    assert data in payloads


def test_quarantine_logging_masks_identifiers(store, caplog):
    token_path = store.get_token_path(6)
    token_path.write_text("{}")

    caplog.set_level(logging.WARNING)
    store._quarantine_file(6, token_path, "corrupted")

    record = next(record for record in caplog.records if record.name == "auth")
    assert "user 6" not in record.getMessage()
    assert token_path.name not in record.getMessage()
    assert getattr(record, "masked_user_id", None) == mask_user_id(6)
    masked_quarantine = getattr(record, "quarantined_mask", None)
    assert masked_quarantine and masked_quarantine.startswith("token-")
