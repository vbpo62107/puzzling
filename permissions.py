from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator, Set as AbstractSet
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set

from dotenv import dotenv_values
from telegram import Update
from telegram.ext import ContextTypes

from monitoring import log_activity

USER_STORE_PATH = Path(os.getenv("USER_STORE_PATH", "data/users.json")).expanduser()
USER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

ADMIN_WHITELIST_KEY = os.getenv("ADMIN_WHITELIST_ENV_VAR", "ADMIN_WHITELIST")
ENV_FILE_PATH = Path(os.getenv("ENV_FILE_PATH", ".env")).expanduser()
WHITELIST_WATCH_INTERVAL = float(os.getenv("WHITELIST_WATCH_INTERVAL", "30"))

logger = logging.getLogger(__name__)

ROLES_ORDER = {"user": 0, "admin": 1, "super_admin": 2}

_store: Dict[str, Dict[str, Any]] = {"users": {}}


class AdminWhitelistManager:
    def __init__(self, env_path: Path, env_key: str, watch_interval: float) -> None:
        self._env_path = env_path
        self._env_key = env_key
        self._watch_interval = max(0.0, watch_interval)
        self._ids: Set[int] = set()
        self._mtime: Optional[float] = None
        self._last_reload: Optional[datetime] = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None
        self.reload(force=True, source="startup")
        self._start_watcher()

    def get_ids(self) -> Set[int]:
        with self._lock:
            return set(self._ids)

    def reload(self, force: bool = False, source: str = "manual") -> bool:
        with self._lock:
            current_mtime = self._get_mtime()
            if not force and self._mtime is not None and current_mtime == self._mtime:
                return False
            self._ids = self._load_ids()
            self._mtime = current_mtime
            self._last_reload = datetime.now(timezone.utc)
            payload = {
                "event": "admin_whitelist_reload",
                "source": source,
                "env_path": str(self._env_path),
                "updated_at": self._last_reload.isoformat(),
                "whitelist": sorted(self._ids),
            }
        logger.info("admin_whitelist.reload %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return True

    def _load_ids(self) -> Set[int]:
        raw_value = ""
        if self._env_path.exists():
            env_values = dotenv_values(self._env_path)
            raw_value = (
                env_values.get(self._env_key)
                or env_values.get("SUPER_ADMIN_IDS")
                or ""
            )
        if not raw_value:
            raw_value = os.getenv(self._env_key, "") or os.getenv("SUPER_ADMIN_IDS", "")
        ids: Set[int] = set()
        for part in raw_value.split(","):
            candidate = part.strip()
            if candidate.isdigit():
                ids.add(int(candidate))
        return ids

    def _get_mtime(self) -> Optional[float]:
        if self._env_path.exists():
            return self._env_path.stat().st_mtime
        return None

    def _start_watcher(self) -> None:
        if self._watch_interval <= 0:
            return
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_thread = threading.Thread(
            target=self._watch_loop,
            name="admin-whitelist-watcher",
            daemon=True,
        )
        self._watch_thread.start()

    def _watch_loop(self) -> None:
        while not self._stop_event.wait(self._watch_interval):
            try:
                self.reload(force=False, source="watcher")
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Failed to refresh admin whitelist: %s", exc)

    def stop(self) -> None:
        self._stop_event.set()
        if self._watch_thread and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=1.0)


class _DynamicSuperAdminSet(AbstractSet[int]):
    def __init__(self, manager: AdminWhitelistManager) -> None:
        self._manager = manager

    def __contains__(self, item: object) -> bool:  # type: ignore[override]
        try:
            candidate = int(item)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return candidate in self._manager.get_ids()

    def __iter__(self) -> Iterator[int]:  # type: ignore[override]
        return iter(sorted(self._manager.get_ids()))

    def __len__(self) -> int:  # type: ignore[override]
        return len(self._manager.get_ids())

    def __repr__(self) -> str:
        return f"DynamicSuperAdminSet({sorted(self._manager.get_ids())})"


_whitelist_manager = AdminWhitelistManager(ENV_FILE_PATH, ADMIN_WHITELIST_KEY, WHITELIST_WATCH_INTERVAL)
DEFAULT_SUPER_ADMINS: AbstractSet[int] = _DynamicSuperAdminSet(_whitelist_manager)


def get_super_admin_whitelist() -> Set[int]:
    return _whitelist_manager.get_ids()


def reload_admin_whitelist(force: bool = True, source: str = "manual") -> bool:
    return _whitelist_manager.reload(force=force, source=source)


def _load_store() -> None:
    if USER_STORE_PATH.exists():
        try:
            data = json.loads(USER_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "users" in data:
                _store["users"] = {
                    str(k): {"role": v.get("role", "user"), "name": v.get("name")}
                    for k, v in data["users"].items()
                }
        except Exception:
            # Fallback to empty store if file cannot be parsed
            _store["users"] = {}
    else:
        _store["users"] = {}

    # Ensure default super admins remain
    for uid in DEFAULT_SUPER_ADMINS:
        _store["users"].setdefault(str(uid), {"role": "super_admin"})


def _save_store() -> None:
    USER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_STORE_PATH.write_text(json.dumps(_store, ensure_ascii=False, indent=2), encoding="utf-8")


def get_user_role(user_id: Optional[int]) -> str:
    if user_id is None:
        return "user"
    if not _store["users"]:
        _load_store()
    if user_id in DEFAULT_SUPER_ADMINS:
        return "super_admin"
    record = _store["users"].get(str(user_id))
    return (record or {}).get("role", "user")


def set_user_role(target_id: int, role: str, name: Optional[str] = None) -> None:
    if not _store["users"]:
        _load_store()
    _store["users"][str(target_id)] = {"role": role, "name": name}
    _save_store()


def remove_user(target_id: int) -> bool:
    if target_id in DEFAULT_SUPER_ADMINS:
        return False
    if not _store["users"]:
        _load_store()
    removed = _store["users"].pop(str(target_id), None)
    if removed is not None:
        _save_store()
        return True
    return False


def list_users() -> Dict[str, Dict[str, Any]]:
    if not _store["users"]:
        _load_store()
    return dict(_store["users"])


def has_permission(user_id: Optional[int], required_role: str) -> bool:
    current_role = get_user_role(user_id)
    return ROLES_ORDER.get(current_role, 0) >= ROLES_ORDER.get(required_role, 0)


def require_role(required_role: str) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id if update.effective_user else None
            chat_id = update.effective_chat.id if update.effective_chat else None
            current_role = get_user_role(user_id)
            command_name = func.__name__
            if ROLES_ORDER.get(current_role, 0) < ROLES_ORDER.get(required_role, 0):
                log_activity(
                    user_id or 0,
                    current_role,
                    command_name,
                    source="permissions.require_role",
                    verification="deny",
                )
                message = "❌ 权限不足，无法执行该操作。"
                if update.message:
                    await update.message.reply_text(message)
                elif chat_id is not None:
                    await context.bot.send_message(chat_id=chat_id, text=message)
                return
            log_activity(
                user_id or 0,
                current_role,
                command_name,
                source="permissions.require_role",
                verification="pass",
            )
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


_load_store()

