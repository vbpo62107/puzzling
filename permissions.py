from __future__ import annotations

import json
import os
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import log_activity
USER_STORE_PATH = Path(os.getenv("USER_STORE_PATH", "data/users.json")).expanduser()
USER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)

DEFAULT_SUPER_ADMINS = {
    int(uid.strip())
    for uid in os.getenv("SUPER_ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
}

ROLES_ORDER = {"user": 0, "admin": 1, "super_admin": 2}

_store: Dict[str, Dict[str, Any]] = {"users": {}}


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
                log_activity(user_id or 0, current_role, command_name, "deny")
                message = "❌ 权限不足，无法执行该操作。"
                if update.message:
                    await update.message.reply_text(message)
                elif chat_id is not None:
                    await context.bot.send_message(chat_id=chat_id, text=message)
                return
            log_activity(user_id or 0, current_role, command_name, "pass")
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


_load_store()
