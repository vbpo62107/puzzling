from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from pydrive2.auth import GoogleAuth

from creds import get_google_token_base_dir, get_user_token_path

logger = logging.getLogger("auth")

DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
)

_CACHE_TTL_SECONDS = 300
_REFRESH_WINDOW = timedelta(hours=24)
_REFRESH_FAILURE_THRESHOLD = 3


class TokenState(Enum):
    ABSENT = "absent"
    VALID = "valid"
    EXPIRED = "expired"
    CORRUPTED = "corrupted"
    REFRESH_FAILED = "refresh_failed"


@dataclass(slots=True)
class TokenLoadResult:
    user_id: int
    path: Path
    state: TokenState
    gauth: Optional[GoogleAuth] = None
    refreshed: bool = False
    error: Optional[str] = None
    quarantined_to: Optional[Path] = None
    latency_ms: float = 0.0

    def as_metadata(self) -> Dict[str, object]:
        return {
            "user_id": self.user_id,
            "path": str(self.path),
            "state": self.state.value,
            "refreshed": self.refreshed,
            "error": self.error,
            "quarantined_to": str(self.quarantined_to) if self.quarantined_to else None,
            "latency_ms": round(self.latency_ms, 3),
        }


@dataclass(slots=True)
class _TokenCacheEntry:
    result: TokenLoadResult
    mtime: float
    size: int
    timestamp: float


class TokenStore:
    def __init__(
        self,
        *,
        base_dir: Optional[Path] = None,
        cache_ttl_seconds: int = _CACHE_TTL_SECONDS,
    ) -> None:
        self._base_dir = (base_dir or get_google_token_base_dir()).expanduser()
        self._cache_ttl = max(1, cache_ttl_seconds)
        self._cache: Dict[int, _TokenCacheEntry] = {}
        self._locks: Dict[int, threading.Lock] = defaultdict(threading.Lock)
        self._refresh_failures: Dict[int, deque[datetime]] = defaultdict(deque)
        self._ensure_base_dir()

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def ensure_token_storage(self, token_file: str | Path) -> None:
        token_path = Path(token_file).expanduser()
        directory = token_path.parent
        if not directory:
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to create token directory %s: %s", directory, exc)
        self._chmod(directory, 0o700)

    def configure_gauth(self, gauth: GoogleAuth, token_file: str | Path) -> GoogleAuth:
        token_path = Path(token_file).expanduser()
        self.ensure_token_storage(token_path)
        settings = gauth.settings
        client_secrets = Path(
            os.getenv(
                "GOOGLE_CLIENT_SECRETS_FILE",
                self._base_dir.parent / "client_secrets.json",
            )
        )

        settings["client_config_backend"] = "file"
        settings["client_config_file"] = str(client_secrets)
        settings["oauth_scope"] = list(DEFAULT_SCOPES)

        settings["save_credentials"] = True
        settings["save_credentials_backend"] = "file"
        settings["save_credentials_file"] = str(token_path)
        settings["save_credentials_dir"] = str(token_path.parent)
        settings["get_refresh_token"] = True

        auth_param = settings.get("auth_param", {}) or {}
        auth_param["access_type"] = "offline"
        auth_param["prompt"] = "consent"
        settings["auth_param"] = auth_param

        return gauth

    def prepare_gauth(self, user_id: int) -> TokenLoadResult:
        """Load and optionally refresh the GoogleAuth for ``user_id``."""

        load_result = self._load(user_id)
        if load_result.state is TokenState.EXPIRED and load_result.gauth is not None:
            refresh_result = self.refresh(user_id, load_result.gauth)
            if refresh_result.state is TokenState.VALID:
                return refresh_result
            return refresh_result
        return load_result

    def refresh(self, user_id: int, gauth: GoogleAuth) -> TokenLoadResult:
        start = time.perf_counter()
        token_path = self.get_token_path(user_id)
        if gauth is None:
            latency_ms = (time.perf_counter() - start) * 1000
            return TokenLoadResult(
                user_id=user_id,
                path=token_path,
                state=TokenState.REFRESH_FAILED,
                gauth=None,
                error="missing_gauth",
                latency_ms=latency_ms,
            )

        with self._user_lock(user_id):
            try:
                gauth.Refresh()
            except Exception as exc:
                result = self._handle_refresh_failure(user_id, token_path, exc, start)
                logger.warning(
                    "🔁 Refresh failed for user %s: %s", user_id, result.error, exc_info=True
                )
                return result

            try:
                self._atomic_save(user_id, gauth, token_path)
            except Exception as exc:
                result = self._handle_refresh_failure(user_id, token_path, exc, start)
                logger.warning(
                    "⚠️ Failed to persist refreshed token for user %s: %s",
                    user_id,
                    result.error,
                    exc_info=True,
                )
                return result

        latency_ms = (time.perf_counter() - start) * 1000
        result = TokenLoadResult(
            user_id=user_id,
            path=token_path,
            state=TokenState.VALID,
            gauth=gauth,
            refreshed=True,
            latency_ms=latency_ms,
        )
        self._refresh_failures[user_id].clear()
        self._update_cache(user_id, result)
        logger.info("🔁 Refreshed access token for user %s", user_id)
        return result

    def store(self, user_id: int, gauth: GoogleAuth) -> TokenLoadResult:
        start = time.perf_counter()
        token_path = self.get_token_path(user_id)
        with self._user_lock(user_id):
            self._atomic_save(user_id, gauth, token_path)
        latency_ms = (time.perf_counter() - start) * 1000
        result = TokenLoadResult(
            user_id=user_id,
            path=token_path,
            state=TokenState.VALID,
            gauth=gauth,
            refreshed=False,
            latency_ms=latency_ms,
        )
        self._refresh_failures[user_id].clear()
        self._update_cache(user_id, result)
        logger.info("💾 Stored credentials for user %s", user_id)
        return result

    def quarantine(self, user_id: int, reason: str) -> Optional[Path]:
        token_path = self.get_token_path(user_id)
        if not token_path.exists():
            return None
        return self._quarantine_file(user_id, token_path, reason)

    def clear_cache(self, user_id: Optional[int] = None) -> None:
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    def get_token_path(self, user_id: int) -> Path:
        return get_user_token_path(int(user_id)).expanduser()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load(self, user_id: int) -> TokenLoadResult:
        start = time.perf_counter()
        token_path = self.get_token_path(user_id)
        self.ensure_token_storage(token_path)

        entry = self._cache.get(user_id)
        token_stat = None
        try:
            token_stat = token_path.stat()
        except FileNotFoundError:
            token_stat = None

        now = time.perf_counter()
        if entry and token_stat:
            if now - entry.timestamp < self._cache_ttl:
                if (
                    entry.mtime == token_stat.st_mtime
                    and entry.size == token_stat.st_size
                ):
                    return entry.result
        elif entry and token_stat is None and now - entry.timestamp < self._cache_ttl:
            return entry.result

        if token_stat is None:
            latency_ms = (time.perf_counter() - start) * 1000
            result = TokenLoadResult(
                user_id=user_id,
                path=token_path,
                state=TokenState.ABSENT,
                gauth=None,
                latency_ms=latency_ms,
            )
            self._update_cache(user_id, result, mtime=0.0, size=0)
            logger.debug("🔐 No credentials found for user %s", user_id)
            return result

        gauth = self.configure_gauth(GoogleAuth(), token_path)
        try:
            gauth.LoadCredentialsFile(str(token_path))
        except Exception as exc:
            quarantined = self._quarantine_file(user_id, token_path, "load_error")
            latency_ms = (time.perf_counter() - start) * 1000
            result = TokenLoadResult(
                user_id=user_id,
                path=token_path,
                state=TokenState.CORRUPTED,
                gauth=None,
                error=str(exc),
                quarantined_to=quarantined,
                latency_ms=latency_ms,
            )
            self._update_cache(user_id, result, mtime=0.0, size=0)
            logger.warning("⚠️ Invalid credentials for user %s: %s", user_id, exc)
            return result

        credentials = getattr(gauth, "credentials", None)
        if credentials is None or getattr(credentials, "invalid", False):
            quarantined = self._quarantine_file(user_id, token_path, "invalid_credentials")
            latency_ms = (time.perf_counter() - start) * 1000
            result = TokenLoadResult(
                user_id=user_id,
                path=token_path,
                state=TokenState.CORRUPTED,
                gauth=None,
                error="invalid_credentials",
                quarantined_to=quarantined,
                latency_ms=latency_ms,
            )
            self._update_cache(user_id, result, mtime=0.0, size=0)
            logger.warning("⚠️ Removed invalid credentials for user %s", user_id)
            return result

        state = TokenState.EXPIRED if gauth.access_token_expired else TokenState.VALID
        latency_ms = (time.perf_counter() - start) * 1000
        result = TokenLoadResult(
            user_id=user_id,
            path=token_path,
            state=state,
            gauth=gauth,
            latency_ms=latency_ms,
        )
        self._update_cache(
            user_id,
            result,
            mtime=token_stat.st_mtime,
            size=token_stat.st_size,
        )
        logger.debug(
            "🔐 Loaded credentials for user %s (state=%s)", user_id, state.value
        )
        return result

    def _atomic_save(self, user_id: int, gauth: GoogleAuth, token_path: Path) -> None:
        self.ensure_token_storage(token_path)
        token_dir = token_path.parent
        with self._file_lock(token_dir / f"{token_path.name}.lock"):
            with tempfile.NamedTemporaryFile(
                "w", delete=False, dir=str(token_dir), prefix="tmp_token_", suffix=".json"
            ) as temp_file:
                temp_path = Path(temp_file.name)
            try:
                gauth.SaveCredentialsFile(str(temp_path))
                os.replace(temp_path, token_path)
                self._chmod(token_path, 0o600)
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    temp_path.unlink()
                raise

    def _quarantine_file(
        self, user_id: int, token_path: Path, reason: str
    ) -> Optional[Path]:
        try:
            quarantine_dir = self._base_dir / "quarantine"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            self._chmod(quarantine_dir, 0o700)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            destination = quarantine_dir / f"{token_path.stem}_{timestamp}.json"
            if token_path.exists():
                token_path.replace(destination)
                logger.warning(
                    "🚫 Quarantined token for user %s due to %s: %s",
                    user_id,
                    reason,
                    destination,
                )
                self._update_cache(user_id, TokenLoadResult(
                    user_id=user_id,
                    path=token_path,
                    state=TokenState.CORRUPTED,
                    gauth=None,
                    error=reason,
                    quarantined_to=destination,
                ), mtime=0.0, size=0)
                return destination
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error(
                "⚠️ Failed to quarantine token for user %s: %s", user_id, exc, exc_info=True
            )
        with contextlib.suppress(FileNotFoundError):
            token_path.unlink()
        return None

    def _handle_refresh_failure(
        self,
        user_id: int,
        token_path: Path,
        exc: Exception,
        start: float,
    ) -> TokenLoadResult:
        now = datetime.now(timezone.utc)
        failures = self._refresh_failures[user_id]
        cutoff = now - _REFRESH_WINDOW
        while failures and failures[0] < cutoff:
            failures.popleft()
        failures.append(now)

        quarantined: Optional[Path] = None
        if len(failures) >= _REFRESH_FAILURE_THRESHOLD:
            quarantined = self._quarantine_file(user_id, token_path, "refresh_failures")

        latency_ms = (time.perf_counter() - start) * 1000
        result = TokenLoadResult(
            user_id=user_id,
            path=token_path,
            state=TokenState.REFRESH_FAILED,
            gauth=None,
            error=str(exc),
            quarantined_to=quarantined,
            latency_ms=latency_ms,
        )
        self._update_cache(user_id, result, mtime=0.0, size=0)
        return result

    def _update_cache(
        self,
        user_id: int,
        result: TokenLoadResult,
        *,
        mtime: Optional[float] = None,
        size: Optional[int] = None,
    ) -> None:
        timestamp = time.perf_counter()
        entry = _TokenCacheEntry(
            result=result,
            mtime=mtime or 0.0,
            size=size or 0,
            timestamp=timestamp,
        )
        self._cache[user_id] = entry

    def _chmod(self, path: Path, mode: int) -> None:
        try:
            os.chmod(path, mode)
        except PermissionError:  # pragma: no cover - best effort
            logger.debug("Skipping chmod for %s", path)
        except FileNotFoundError:  # pragma: no cover - best effort
            return

    def _ensure_base_dir(self) -> None:
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Unable to create token base directory %s: %s", self._base_dir, exc)
        self._chmod(self._base_dir, 0o700)

    @contextlib.contextmanager
    def _file_lock(self, lock_path: Path, timeout: float = 5.0):
        start = time.perf_counter()
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                if time.perf_counter() - start > timeout:
                    raise TimeoutError(f"Timed out waiting for lock {lock_path}")
                time.sleep(0.1)
        try:
            yield
        finally:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(lock_path)

    @contextlib.contextmanager
    def _user_lock(self, user_id: int):
        lock = self._locks[user_id]
        acquired = lock.acquire(timeout=5.0)
        if not acquired:
            raise TimeoutError(f"Unable to acquire lock for user {user_id}")
        try:
            yield
        finally:
            lock.release()


_token_store = TokenStore()


def ensure_token_storage(token_file: str | Path) -> None:
    _token_store.ensure_token_storage(token_file)


def configure_gauth(gauth: GoogleAuth, token_file: str | Path) -> GoogleAuth:
    return _token_store.configure_gauth(gauth, token_file)


def prepare_gauth(user_id: int) -> TokenLoadResult:
    return _token_store.prepare_gauth(user_id)


def store_gauth(user_id: int, gauth: GoogleAuth) -> TokenLoadResult:
    return _token_store.store(user_id, gauth)


def refresh_gauth(user_id: int, gauth: GoogleAuth) -> TokenLoadResult:
    return _token_store.refresh(user_id, gauth)


def get_token_path(user_id: int) -> Path:
    return _token_store.get_token_path(user_id)


def token_store() -> TokenStore:
    return _token_store


__all__ = [
    "DEFAULT_SCOPES",
    "TokenState",
    "TokenLoadResult",
    "TokenStore",
    "configure_gauth",
    "ensure_token_storage",
    "prepare_gauth",
    "refresh_gauth",
    "store_gauth",
    "get_token_path",
    "token_store",
]
