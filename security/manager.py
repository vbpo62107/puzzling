from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from dotenv import dotenv_values

from creds import get_google_token_base_dir, get_user_token_path
from permissions import has_permission
from .rate_limiter import RATE_LIMIT_CLASS_MAP, RATE_LIMITS, RateLimiterService

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
    metadata: Optional[Dict[str, Any]] = None

    # Decision constants
    ALLOW = "allow"

    DENY_UNAUTHORIZED = "deny_unauthorized"
    DENY_UNAUTHORIZED_MISSING_USER = f"{DENY_UNAUTHORIZED}/missing_user"
    DENY_UNAUTHORIZED_TOKEN_MISSING = f"{DENY_UNAUTHORIZED}/token_missing"
    DENY_UNAUTHORIZED_ADMIN_REQUIRED = f"{DENY_UNAUTHORIZED}/admin_required"

    DENY_NOT_WHITELISTED = "deny_not_whitelisted"
    NOT_IN_WHITELIST = DENY_NOT_WHITELISTED

    RATE_LIMITED = "rate_limited"

    POLICY_ERROR = "policy_error"
    POLICY_ERROR_UNSUPPORTED_LEVEL = f"{POLICY_ERROR}/unsupported_level"


class PermissionManager:
    """Centralise whitelist and token-driven access decisions."""

    def __init__(
        self,
        env_path: Path = DEFAULT_ENV_PATH,
        whitelist_keys: Iterable[str] = DEFAULT_WHITELIST_KEYS,
        *,
        cache_ttl_seconds: int = 30,
        rate_limit_service: Optional[RateLimiterService] = None,
        rate_limits: Optional[Dict[str, Any]] = None,
        rate_limit_class_map: Optional[Dict[str, Any]] = None,
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
        if rate_limit_service is not None:
            self._rate_limiter = rate_limit_service
        else:
            limits_config = rate_limits if rate_limits is not None else RATE_LIMITS
            class_map = rate_limit_class_map if rate_limit_class_map is not None else RATE_LIMIT_CLASS_MAP
            self._rate_limiter = RateLimiterService.from_settings(limits_config, class_map)

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

    def evaluate_access(
        self,
        user_id: Optional[int],
        level: SecurityLevel,
        *,
        command_name: Optional[str] = None,
    ) -> AccessDecision:
        if user_id is None:
            return AccessDecision(False, AccessDecision.DENY_UNAUTHORIZED_MISSING_USER)

        if level is SecurityLevel.PUBLIC:
            limited = self._maybe_rate_limit(user_id, level, command_name)
            if limited:
                return limited
            return AccessDecision(True, AccessDecision.ALLOW, via="public")

        is_whitelisted = self.is_whitelisted(user_id)

        if level is SecurityLevel.ADMIN:
            if not is_whitelisted:
                return AccessDecision(False, AccessDecision.DENY_NOT_WHITELISTED)
            if not has_permission(user_id, "admin"):
                return AccessDecision(False, AccessDecision.DENY_UNAUTHORIZED_ADMIN_REQUIRED)
            limited = self._maybe_rate_limit(user_id, level, command_name)
            if limited:
                return limited
            return AccessDecision(True, AccessDecision.ALLOW, via="whitelist")

        if is_whitelisted:
            limited = self._maybe_rate_limit(user_id, level, command_name)
            if limited:
                return limited
            return AccessDecision(True, AccessDecision.ALLOW, via="whitelist")

        if not self._has_token_cached(user_id):
            return AccessDecision(False, AccessDecision.DENY_UNAUTHORIZED_TOKEN_MISSING)

        if level is SecurityLevel.AUTHORIZED:
            limited = self._maybe_rate_limit(user_id, level, command_name)
            if limited:
                return limited
            return AccessDecision(True, AccessDecision.ALLOW, via="token")

        return AccessDecision(False, AccessDecision.POLICY_ERROR_UNSUPPORTED_LEVEL)

    def _has_token_cached(self, user_id: int) -> bool:
        bucket = int(time.time() // self._cache_ttl)
        return self._token_lookup(bucket, user_id)

    @lru_cache(maxsize=1000)
    def _token_lookup(self, bucket: int, user_id: int) -> bool:
        return self.has_token(user_id)

    def _maybe_rate_limit(
        self,
        user_id: int,
        level: SecurityLevel,
        command_name: Optional[str],
    ) -> Optional[AccessDecision]:
        if not command_name or self._rate_limiter is None:
            return None

        outcome = self._rate_limiter.check(command_name, user_id, level)
        if outcome.allowed:
            return None

        metadata: Dict[str, Any] = {
            "command": command_name,
            "limit": outcome.limit_name,
            "retry_after": outcome.retry_after,
            "cooldown_seconds": outcome.cooldown_seconds,
            "limit_size": outcome.limit,
            "interval_seconds": outcome.interval_seconds,
            "scope": outcome.scope,
            "level": level.value,
        }

        via = f"rate_limit:{outcome.limit_name}" if outcome.limit_name else "rate_limit"
        return AccessDecision(False, AccessDecision.RATE_LIMITED, via=via, metadata=metadata)


permission_manager = PermissionManager()

__all__ = [
    "AccessDecision",
    "PermissionManager",
    "SecurityLevel",
    "permission_manager",
]
