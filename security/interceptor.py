from __future__ import annotations

import enum
import logging
from functools import wraps
from typing import Awaitable, Callable, Dict, Optional, Set

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import log_activity
from permissions import (
    AuthorizationStatus,
    check_authorization,
    get_user_role,
    has_permission,
)


class SecurityLevel(enum.Enum):
    PUBLIC = "public"
    AUTHORIZED = "authorized"
    ADMIN = "admin"


_DENY_MESSAGES = {
    "unknown": "❌ 无法识别您的身份，请稍后重试。",
    "whitelist": "❌ 您尚未加入白名单，无法使用此功能。请联系管理员申请访问。",
    "token": "❌ 您尚未完成授权，请先发送 /auth 按照提示完成授权。",
    "admin": "❌ 您缺少管理员权限，无法执行此指令。",
}


class SecurityInterceptor:
    """Middleware-style security gate for Telegram handlers."""

    def __init__(self) -> None:
        self._levels: Dict[str, SecurityLevel] = {
            "start": SecurityLevel.PUBLIC,
            "help": SecurityLevel.PUBLIC,
            "updates": SecurityLevel.PUBLIC,
            "ping": SecurityLevel.PUBLIC,
            "auth": SecurityLevel.AUTHORIZED,
            "token": SecurityLevel.AUTHORIZED,
            "revoke_tok": SecurityLevel.AUTHORIZED,
            "upload": SecurityLevel.AUTHORIZED,
            "handle_file_message": SecurityLevel.AUTHORIZED,
            "my_status": SecurityLevel.AUTHORIZED,
            "cancel": SecurityLevel.AUTHORIZED,
            "status": SecurityLevel.ADMIN,
            "show_logs": SecurityLevel.ADMIN,
            "add_user": SecurityLevel.ADMIN,
            "remove_user_command": SecurityLevel.ADMIN,
            "list_users_command": SecurityLevel.ADMIN,
            "cleanup_tokens": SecurityLevel.ADMIN,
        }
        self._token_optional: Set[str] = {
            "auth",
            "token",
            "revoke_tok",
            "start",
            "help",
            "updates",
            "ping",
        }

    def set_level(self, name: str, level: SecurityLevel) -> None:
        self._levels[name] = level

    def wrap(
        self,
        name: str,
        callback: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
        level: Optional[SecurityLevel] = None,
    ) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
        actual_level = level or self._levels.get(name, SecurityLevel.PUBLIC)
        requires_token = (
            actual_level == SecurityLevel.AUTHORIZED and name not in self._token_optional
        )

        @wraps(callback)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id if update.effective_user else None
            chat_id = update.effective_chat.id if update.effective_chat else None

            if actual_level == SecurityLevel.PUBLIC:
                return await callback(update, context, *args, **kwargs)

            if user_id is None:
                await self._notify(context, chat_id, _DENY_MESSAGES["unknown"])
                return

            auth_status = check_authorization(user_id)
            user_role = get_user_role(user_id)
            command_name = name

            if not auth_status.whitelisted and not auth_status.has_token:
                self._log_denial(user_id, user_role, command_name, "not_whitelisted", auth_status)
                await self._notify(context, chat_id, _DENY_MESSAGES["whitelist"])
                return

            if requires_token and not auth_status.has_token:
                self._log_denial(user_id, user_role, command_name, "missing_token", auth_status)
                await self._notify(context, chat_id, _DENY_MESSAGES["token"])
                return

            if actual_level == SecurityLevel.ADMIN and not has_permission(user_id, "admin"):
                self._log_denial(user_id, user_role, command_name, "not_admin", auth_status)
                await self._notify(context, chat_id, _DENY_MESSAGES["admin"])
                return

            log_activity(user_id, user_role, f"security_pass:{command_name}")
            return await callback(update, context, *args, **kwargs)

        return wrapper

    async def _notify(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: Optional[int], message: str
    ) -> None:
        if chat_id is None:
            logging.warning("Unable to send denial message: missing chat_id")
            return
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
        except Exception as exc:  # pragma: no cover - defensive
            logging.warning("Failed to send denial message: %s", exc)

    def _log_denial(
        self,
        user_id: int,
        role: str,
        command: str,
        reason: str,
        status: AuthorizationStatus,
    ) -> None:
        detail = f"reason={reason} whitelisted={status.whitelisted} token={status.has_token}"
        log_activity(user_id, role, f"security_deny:{command}", detail)


security_interceptor = SecurityInterceptor()
