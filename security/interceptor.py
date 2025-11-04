from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import log_activity
from permissions import get_user_role

from .manager import AccessDecision, PermissionManager, SecurityLevel, permission_manager

logger = logging.getLogger(__name__)

DENIAL_MESSAGES = {
    "missing_user": "❌ 无法识别您的身份，请稍后再试。",
    "token_missing": "❌ 请先发送 /auth 完成授权后再使用此功能。",
    "admin_required": "❌ 权限不足，仅管理员可用。",
    "not_in_whitelist": "❌ 您尚未在管理员白名单中，请联系管理员。",
    "unsupported_level": "❌ 当前操作暂不支持，请联系管理员。",
}


def _resolve_ids(update: Update) -> tuple[int | None, int | None]:
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    if user_id is None:
        user_id = chat_id
    return user_id, chat_id


async def _send_denial(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    if not message:
        return
    if update.effective_message:
        await update.effective_message.reply_text(message)
    elif update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message)


def secure(
    command_name: str,
    level: SecurityLevel,
    *,
    manager: PermissionManager = permission_manager,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Wrap a handler with whitelist/token enforcement."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> Any:
            user_id, chat_id = _resolve_ids(update)
            decision: AccessDecision = manager.evaluate_access(user_id, level)
            role = get_user_role(user_id) if user_id is not None else "unknown"

            if not decision.allowed:
                message = DENIAL_MESSAGES.get(decision.reason, "❌ 无法执行该操作。")
                await _send_denial(update, context, message)
                log_activity(
                    user_id or 0,
                    role,
                    command_name,
                    source="security.interceptor",
                    verification=decision.reason,
                )
                logger.info(
                    "Denied %s for user=%s reason=%s level=%s chat=%s",
                    command_name,
                    user_id,
                    decision.reason,
                    level.value,
                    chat_id,
                )
                return None

            logger.debug(
                "Access granted for %s via %s (user=%s role=%s)",
                command_name,
                decision.via,
                user_id,
                role,
            )
            return await func(update, context, *args, **kwargs)

        return wrapper

    return decorator


__all__ = ["secure"]
