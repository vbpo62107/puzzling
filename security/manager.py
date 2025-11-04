from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple

from dotenv import dotenv_values

from creds import get_google_token_base_dir, get_user_token_path
from permissions import has_permission

logger = logging.getLogger(__name__)

DEFAULT_ENV_PATH = Path(os.getenv("ENV_FILE_PATH", ".env")).expanduser()
DEFAULT_WHITELIST_KEYS: Tuple[str, ...] = (
    "USER_WHITELIST",
    "AUTHORIZED_USER_IDS",
    "AUTHORIZED_WHITELIST",
    "WHITELIST",
)


class SecurityLevel(Enum):
    PUBLIC = "public"
    AUTHORIZED = "authorized"
    ADMIN = "admin"


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    via: Optional[str] = None

    # Denial reasons
    NOT_IN_WHITELIST = "not_in_whitelist"
    DENY_NOT_WHITELISTED = NOT_IN_WHITELIST


class PermissionManager:
    """Centralise whitelist and token-driven access decisions."""

    def __init__(
        self,
        env_path: Path = DEFAULT_ENV_PATH,
        whitelist_keys: Iterable[str] = DEFAULT_WHITELIST_KEYS,
        *,
        cache_ttl_seconds: int = 30,
    ) -> None:
        self._env_path = env_path
        self._whitelist_keys = tuple(dict.fromkeys(whitelist_keys))
        self._cache_ttl = max(1, cache_ttl_seconds)
        self._whitelist_ids: Set[int] = set()
        self._token_ids: Set[int] = set()
        self._token_base_dir = get_google_token_base_dir()
        self._ensure_token_dir()
        self.reload_whitelist()
        self._preload_token_ids()

    def _ensure_token_dir(self) -> None:
        try:
            self._token_base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Failed to prepare token directory %s: %s", self._token_base_dir, exc)

    def reload_whitelist(self) -> None:
        """Reload the whitelist from .env and environment variables."""

        values = {}
        if self._env_path.exists():
            try:
                values = dotenv_values(self._env_path)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Failed to read %s: %s", self._env_path, exc)
        ids: Set[int] = set()
        for key in self._whitelist_keys:
            raw = (values.get(key) if values else None) or os.getenv(key) or ""
            ids.update(self._parse_id_list(raw))
        if ids != self._whitelist_ids:
            logger.info("Loaded %s whitelist IDs: %s", len(ids), sorted(ids))
        self._whitelist_ids = ids
        self._token_lookup.cache_clear()

    def _preload_token_ids(self) -> None:
        if not self._token_base_dir.exists():
            return
        for path in self._token_base_dir.glob("token_*.json"):
            suffix = path.stem.split("token_")[-1]
            if suffix.isdigit():
                self._token_ids.add(int(suffix))
        if self._token_ids:
            logger.info(
                "Preloaded %s user token(s) from %s",
                len(self._token_ids),
                self._token_base_dir,
            )

    @staticmethod
    def _parse_id_list(raw: str) -> Set[int]:
        ids: Set[int] = set()
        for part in raw.split(","):
            candidate = part.strip()
            if candidate.isdigit():
                ids.add(int(candidate))
        return ids

    def is_whitelisted(self, user_id: int) -> bool:
        return user_id in self._whitelist_ids

    def has_token(self, user_id: int) -> bool:
        token_path = get_user_token_path(user_id)
        if user_id in self._token_ids and token_path.exists():
            return True
        if not token_path.exists():
            self._token_ids.discard(user_id)
            return False
        self._token_ids.add(user_id)
        return True

    def register_token(self, user_id: int) -> None:
        self._token_ids.add(user_id)
        self._token_lookup.cache_clear()

    def unregister_token(self, user_id: int) -> None:
        self._token_ids.discard(user_id)
        self._token_lookup.cache_clear()

    def evaluate_access(self, user_id: Optional[int], level: SecurityLevel) -> AccessDecision:
        if user_id is None:
            return AccessDecision(False, "missing_user")

        if level is SecurityLevel.PUBLIC:
            return AccessDecision(True, "public")

        is_whitelisted = self.is_whitelisted(user_id)

        if level is SecurityLevel.ADMIN:
            if not is_whitelisted:
                return AccessDecision(False, AccessDecision.NOT_IN_WHITELIST)
            if not has_permission(user_id, "admin"):
                return AccessDecision(False, "admin_required")
            return AccessDecision(True, "admin", via="whitelist")

        if is_whitelisted:
            return AccessDecision(True, "whitelist", via="whitelist")

        if not self._has_token_cached(user_id):
            return AccessDecision(False, "token_missing")

        if level is SecurityLevel.AUTHORIZED:
            return AccessDecision(True, "token", via="token")

        return AccessDecision(False, "unsupported_level")

    def _has_token_cached(self, user_id: int) -> bool:
        bucket = int(time.time() // self._cache_ttl)
        return self._token_lookup(bucket, user_id)

    @lru_cache(maxsize=1000)
    def _token_lookup(self, bucket: int, user_id: int) -> bool:
        return self.has_token(user_id)


permission_manager = PermissionManager()

__all__ = [
    "AccessDecision",
    "PermissionManager",
    "SecurityLevel",
    "permission_manager",
]
