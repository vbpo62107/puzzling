from __future__ import annotations

import logging
import threading
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Optional

from telegram import Update
from telegram.ext import ContextTypes

from monitoring import log_activity, record_rate_limit_hit
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


_RATE_LIMIT_NOTICE_LOCK = threading.Lock()
_RATE_LIMIT_NOTICE_STATE: dict[tuple[int, str], float] = {}


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


def _should_send_rate_limit_notice(user_id: Optional[int], limit_name: Optional[str], cooldown: float) -> bool:
    if user_id is None or not limit_name or cooldown <= 0:
        return True

    now = time.monotonic()
    key = (user_id, limit_name)
    with _RATE_LIMIT_NOTICE_LOCK:
        previous = _RATE_LIMIT_NOTICE_STATE.get(key)
        if previous is not None and now - previous < cooldown:
            return False
        _RATE_LIMIT_NOTICE_STATE[key] = now
    return True


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
            decision: AccessDecision = manager.evaluate_access(
                user_id, level, command_name=command_name
            )
            role = get_user_role(user_id) if user_id is not None else "unknown"
            metadata = decision.metadata or {}

            if not decision.allowed:
                should_notify = True
                if decision.reason == AccessDecision.RATE_LIMITED:
                    limit_name = str(metadata.get("limit") or command_name or "unknown")
                    retry_after = metadata.get("retry_after")
                    record_rate_limit_hit(
                        command_name,
                        limit_name,
                        user_id,
                        role,
                        retry_after,
                        metadata if metadata else None,
                    )
                    cooldown = float(metadata.get("cooldown_seconds") or 0.0)
                    should_notify = _should_send_rate_limit_notice(
                        user_id, limit_name, cooldown
                    )

                if should_notify:
                    message = DENIAL_MESSAGES.get(
                        decision.reason, "❌ Unable to perform this action."
                    )
                    await _send_denial(update, context, message)

                log_kwargs = {
                    "source": "security.interceptor",
                    "verification": decision.reason,
                }
                if decision.reason == AccessDecision.RATE_LIMITED and metadata:
                    log_kwargs["metadata"] = metadata

                log_activity(user_id or 0, role, command_name, **log_kwargs)
                logger.info(
                    "Denied %s for user=%s reason=%s level=%s chat=%s metadata=%s",
                    command_name,
                    user_id,
                    decision.reason,
                    level.value,
                    chat_id,
                    metadata or None,
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
