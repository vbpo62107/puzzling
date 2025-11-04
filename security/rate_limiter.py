from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitHit:
    """Represents the outcome of a limiter check."""

    allowed: bool
    retry_after: float = 0.0


@dataclass
class _WindowState:
    count: int
    reset_at: float
    cooldown_until: float = 0.0


class FixedWindowRateLimiter:
    """Fixed window counter with optional cooldown enforcement."""

    def __init__(
        self,
        *,
        limit: int,
        interval: float,
        cooldown: float = 0.0,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if interval <= 0:
            raise ValueError("interval must be greater than zero")

        self._limit = int(limit)
        self._interval = float(interval)
        self._cooldown = max(0.0, float(cooldown))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._windows: Dict[str, _WindowState] = {}

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def cooldown(self) -> float:
        return self._cooldown

    def hit(self, key: str, *, weight: int = 1, now: Optional[float] = None) -> RateLimitHit:
        if weight <= 0:
            raise ValueError("weight must be positive")

        current = self._clock() if now is None else now

        with self._lock:
            state = self._windows.get(key)
            if state is None or current >= state.reset_at:
                state = _WindowState(count=0, reset_at=current + self._interval)
            if state.cooldown_until and current < state.cooldown_until:
                retry_after = max(state.cooldown_until - current, 0.0)
                self._windows[key] = state
                return RateLimitHit(False, retry_after)

            state.count += weight
            if state.count > self._limit:
                if self._cooldown:
                    state.cooldown_until = max(state.cooldown_until, current + self._cooldown)
                retry_after = max(state.reset_at, state.cooldown_until) - current
                self._windows[key] = state
                return RateLimitHit(False, max(retry_after, 0.0))

            self._windows[key] = state
            return RateLimitHit(True, max(state.reset_at - current, 0.0))


class RateLimiterOutcome:
    """Result of checking a command against configured limits."""

    __slots__ = (
        "allowed",
        "limit_name",
        "retry_after",
        "cooldown_seconds",
        "limit",
        "interval_seconds",
        "scope",
    )

    def __init__(
        self,
        *,
        allowed: bool,
        limit_name: Optional[str] = None,
        retry_after: float = 0.0,
        cooldown_seconds: float = 0.0,
        limit: Optional[int] = None,
        interval_seconds: Optional[float] = None,
        scope: Optional[str] = None,
    ) -> None:
        self.allowed = allowed
        self.limit_name = limit_name
        self.retry_after = retry_after
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self.limit = limit
        self.interval_seconds = interval_seconds
        self.scope = scope

    @classmethod
    def allow(cls) -> "RateLimiterOutcome":
        return cls(allowed=True)


class RateLimiterService:
    """Centralised rate limiter driven by the RATE_LIMITS configuration."""

    def __init__(
        self,
        config: Mapping[str, Any],
        class_map: Mapping[str, Callable[..., FixedWindowRateLimiter]],
    ) -> None:
        self._limits: Dict[str, list[Dict[str, Any]]] = {}
        self._entries: Dict[str, list[tuple[str, FixedWindowRateLimiter, Dict[str, Any]]]] = {}
        self._class_map = class_map
        self._load_config(config)

    def _load_config(self, config: Mapping[str, Any]) -> None:
        for command, raw_entry in config.items():
            entries = raw_entry if isinstance(raw_entry, Sequence) and not isinstance(raw_entry, (str, bytes)) else [raw_entry]
            parsed_entries: list[tuple[str, FixedWindowRateLimiter, Dict[str, Any]]] = []
            for index, entry in enumerate(entries):
                if not isinstance(entry, Mapping):
                    logger.warning("Invalid rate limit entry for %s: %r", command, entry)
                    continue
                parsed = self._build_entry(command, index, entry)
                if parsed is not None:
                    parsed_entries.append(parsed)
            if parsed_entries:
                self._entries[command] = parsed_entries

    def _build_entry(
        self,
        command: str,
        index: int,
        entry: Mapping[str, Any],
    ) -> Optional[tuple[str, FixedWindowRateLimiter, Dict[str, Any]]]:
        class_name = str(entry.get("class", "fixed_window"))
        limiter_cls = self._class_map.get(class_name)
        if limiter_cls is None:
            logger.warning("Unknown rate limiter class %s for command %s", class_name, command)
            return None

        try:
            limit_value = int(entry["limit"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Missing or invalid limit for command %s entry %s", command, index)
            return None

        interval_value = self._extract_interval(entry)
        if interval_value is None:
            logger.warning("Missing interval configuration for command %s entry %s", command, index)
            return None

        cooldown_value = self._extract_float(entry, ("cooldown_seconds", "cooldown"), default=0.0)

        limiter_kwargs = {
            "limit": limit_value,
            "interval": interval_value,
            "cooldown": cooldown_value,
        }
        limiter = limiter_cls(**limiter_kwargs)

        name = str(entry.get("name") or f"{command}:{index}")
        levels = self._normalise_levels(entry.get("levels"))
        scope = str(entry.get("scope", "user")).lower()

        metadata = {
            "levels": levels,
            "scope": scope,
            "cooldown_seconds": cooldown_value,
            "limit": limiter.limit,
            "interval_seconds": limiter.interval,
        }

        return name, limiter, metadata

    @staticmethod
    def _extract_interval(entry: Mapping[str, Any]) -> Optional[float]:
        for key in ("interval", "interval_seconds", "window", "window_seconds", "per", "per_seconds"):
            if key in entry:
                try:
                    value = float(entry[key])
                except (TypeError, ValueError):
                    return None
                if value > 0:
                    return value
                return None
        return None

    @staticmethod
    def _extract_float(entry: Mapping[str, Any], keys: Iterable[str], default: float = 0.0) -> float:
        for key in keys:
            if key in entry:
                try:
                    return float(entry[key])
                except (TypeError, ValueError):
                    return default
        return default

    @staticmethod
    def _normalise_levels(value: Any) -> set[str]:
        if isinstance(value, (list, tuple, set)):
            return {str(item).lower() for item in value}
        if isinstance(value, str):
            return {value.lower()}
        return set()

    @property
    def enabled(self) -> bool:
        return bool(self._entries)

    def check(
        self,
        command: str,
        user_id: Optional[int],
        level: Any,
    ) -> RateLimiterOutcome:
        if not self._entries:
            return RateLimiterOutcome.allow()

        entries = self._entries.get(command)
        if not entries:
            return RateLimiterOutcome.allow()

        level_key = str(getattr(level, "value", level)).lower()
        level_name = str(getattr(level, "name", level)).lower()

        for name, limiter, metadata in entries:
            levels: set[str] = metadata.get("levels", set())  # type: ignore[assignment]
            if levels and level_key not in levels and level_name not in levels:
                continue

            scope = metadata.get("scope", "user")
            key = self._build_key(command, scope, user_id)
            if key is None:
                continue

            hit = limiter.hit(key)
            if not hit.allowed:
                return RateLimiterOutcome(
                    allowed=False,
                    limit_name=name,
                    retry_after=hit.retry_after,
                    cooldown_seconds=float(metadata.get("cooldown_seconds", 0.0)),
                    limit=int(metadata.get("limit") or limiter.limit),
                    interval_seconds=float(metadata.get("interval_seconds") or limiter.interval),
                    scope=str(scope),
                )

        return RateLimiterOutcome.allow()

    @staticmethod
    def _build_key(command: str, scope: str, user_id: Optional[int]) -> Optional[str]:
        scope_key = scope.lower()
        if scope_key == "global":
            return f"global:{command}"
        if scope_key == "user":
            if user_id is None:
                return None
            return f"user:{command}:{user_id}"
        # Fallback to user scope if possible
        if user_id is None:
            return f"{scope_key}:{command}"
        return f"{scope_key}:{command}:{user_id}"

    @classmethod
    def from_settings(
        cls,
        config: Optional[Mapping[str, Any]] = None,
        class_map: Optional[Mapping[str, Callable[..., FixedWindowRateLimiter]]] = None,
    ) -> Optional["RateLimiterService"]:
        if not config:
            return None
        mapping = dict(config)
        if not mapping:
            return None
        limiter_map = dict(class_map or RATE_LIMIT_CLASS_MAP)
        return cls(mapping, limiter_map)


def _load_rate_limits(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse RATE_LIMITS configuration")
        return {}
    if not isinstance(parsed, Mapping):
        logger.warning("RATE_LIMITS must be a JSON object, got %s", type(parsed).__name__)
        return {}
    return dict(parsed)


RATE_LIMIT_CLASS_MAP: Dict[str, Callable[..., FixedWindowRateLimiter]] = {
    "fixed_window": FixedWindowRateLimiter,
}

RATE_LIMITS: Dict[str, Any] = _load_rate_limits(os.getenv("RATE_LIMITS"))


__all__ = [
    "FixedWindowRateLimiter",
    "RATE_LIMITS",
    "RATE_LIMIT_CLASS_MAP",
    "RateLimiterOutcome",
    "RateLimiterService",
]
