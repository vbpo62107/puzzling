from __future__ import annotations

import logging
import time
import uuid
from functools import wraps
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import log_security_audit
from permissions import get_user_role

from .manager import AccessDecision, PermissionManager, SecurityLevel, permission_manager

logger = logging.getLogger(__name__)

DENIAL_MESSAGES = {
    AccessDecision.DENY_UNAUTHORIZED_MISSING_USER:
        "❌ I couldn't verify who requested this. Please try again in a private chat.",
    AccessDecision.DENY_UNAUTHORIZED_TOKEN_MISSING:
        "❌ Please authenticate with /auth before using this command.",
    AccessDecision.DENY_UNAUTHORIZED_ADMIN_REQUIRED:
        "❌ This 🔴 command is reserved for admins. In group chats, run it in a private chat with the bot.",
    AccessDecision.DENY_NOT_WHITELISTED:
        "❌ You're not on the admin whitelist yet. Please contact an administrator.",
    AccessDecision.RATE_LIMITED:
        "❌ You're sending requests too quickly. Please slow down and try again.",
    AccessDecision.POLICY_ERROR_UNSUPPORTED_LEVEL:
        "❌ This request isn't supported. Please contact an administrator.",
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
            started = time.perf_counter()
            decision: AccessDecision = manager.evaluate_access(user_id, level)
            role = get_user_role(user_id) if user_id is not None else "unknown"
            chat_type = (
                update.effective_chat.type if update.effective_chat else "unknown"
            )
            policy_version = getattr(manager, "policy_version", None)
            whitelist_version = getattr(manager, "whitelist_version", None)
            corr_id = uuid.uuid4().hex

            if not decision.allowed:
                message = DENIAL_MESSAGES.get(decision.reason, "❌ Unable to perform this action.")
                await _send_denial(update, context, message)
                duration_ms = (time.perf_counter() - started) * 1000
                log_security_audit(
                    ts=None,
                    user_id=user_id,
                    chat_type=chat_type,
                    command=command_name,
                    decision="deny",
                    reason=decision.reason,
                    duration_ms=duration_ms,
                    policy_version=policy_version,
                    whitelist_version=whitelist_version,
                    corr_id=corr_id,
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

            try:
                result = await func(update, context, *args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - started) * 1000
                log_security_audit(
                    ts=None,
                    user_id=user_id,
                    chat_type=chat_type,
                    command=command_name,
                    decision="allow",
                    reason=decision.reason,
                    duration_ms=duration_ms,
                    policy_version=policy_version,
                    whitelist_version=whitelist_version,
                    corr_id=corr_id,
                )
                logger.debug(
                    "Access granted for %s via %s (user=%s role=%s corr_id=%s)",
                    command_name,
                    decision.via,
                    user_id,
                    role,
                    corr_id,
                )
            return result

        return wrapper

    return decorator


__all__ = ["secure"]
