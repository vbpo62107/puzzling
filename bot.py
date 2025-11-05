#!/usr/bin/env python3

import logging
import os
from datetime import timedelta

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from creds import TELEGRAM_BOT_TOKEN, require_bot_credentials
from handlers.auth_handler import auth, revoke_tok, token
from handlers.file_handler import handle_file_message
from handlers.status_handler import (
    cancel,
    help as help_command,
    my_status,
    ping,
    start,
    status,
    updates,
)
from handlers.upload_handler import upload
from handlers.admin_handler import (
    add_user,
    cleanup_tokens as cleanup_tokens_command,
    list_users_command,
    reload_whitelist,
    remove_user_command,
    search_logs_command,
    show_logs,
)
from monitoring import log_system_info, setup_logging, trigger_admin_alert
from puzzling.token_cleanup import run_cleanup
from security.interceptor import secure
from security.manager import SecurityLevel
from security.maintenance import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_REFRESH_AHEAD,
    run_token_health_check,
)

LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()
setup_logging(LOG_LEVEL_NAME)

logging.info("🤖 机器人启动中…")


def build_application():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    def guard(name: str, level: SecurityLevel, func):
        return secure(name, level)(func)

    application.add_handler(CommandHandler("start", guard("start", SecurityLevel.PUBLIC, start)))
    application.add_handler(CommandHandler("help", guard("help", SecurityLevel.PUBLIC, help_command)))
    application.add_handler(CommandHandler("auth", guard("auth", SecurityLevel.PUBLIC, auth)))
    application.add_handler(CommandHandler("revoke", guard("revoke", SecurityLevel.AUTHORIZED, revoke_tok)))
    application.add_handler(CommandHandler("update", guard("update", SecurityLevel.PUBLIC, updates)))
    application.add_handler(CommandHandler("mystatus", guard("mystatus", SecurityLevel.AUTHORIZED, my_status)))
    application.add_handler(CommandHandler("status", guard("status", SecurityLevel.ADMIN, status)))
    application.add_handler(CommandHandler("cancel", guard("cancel", SecurityLevel.AUTHORIZED, cancel)))
    application.add_handler(CommandHandler("ping", guard("ping", SecurityLevel.PUBLIC, ping)))
    application.add_handler(CommandHandler("logs", guard("logs", SecurityLevel.ADMIN, show_logs)))
    application.add_handler(
        CommandHandler(
            "search_logs",
            guard("search_logs", SecurityLevel.ADMIN, search_logs_command),
        )
    )
    application.add_handler(CommandHandler("adduser", guard("adduser", SecurityLevel.ADMIN, add_user)))
    application.add_handler(CommandHandler("removeuser", guard("removeuser", SecurityLevel.ADMIN, remove_user_command)))
    application.add_handler(CommandHandler("users", guard("users", SecurityLevel.ADMIN, list_users_command)))
    application.add_handler(CommandHandler("cleanup", guard("cleanup", SecurityLevel.ADMIN, cleanup_tokens_command)))
    application.add_handler(
        CommandHandler(
            "reload_whitelist",
            guard("reload_whitelist", SecurityLevel.ADMIN, reload_whitelist),
        )
    )

    application.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.PHOTO),
            guard("file_upload", SecurityLevel.AUTHORIZED, handle_file_message),
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"http"),
            guard("upload_url", SecurityLevel.AUTHORIZED, upload),
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"http"),
            guard("token", SecurityLevel.PUBLIC, token),
        )
    )

    job_queue = application.job_queue
    if job_queue is not None:
        interval_raw = os.getenv("TOKEN_MAINTENANCE_INTERVAL_MINUTES", "15")
        try:
            interval_minutes = max(1, int(interval_raw))
        except ValueError:
            logging.warning(
                "Invalid TOKEN_MAINTENANCE_INTERVAL_MINUTES value %r; defaulting to 15",
                interval_raw,
            )
            interval_minutes = 15

        batch_raw = os.getenv("TOKEN_MAINTENANCE_BATCH_SIZE")
        refresh_raw = os.getenv("TOKEN_MAINTENANCE_REFRESH_AHEAD_MINUTES")

        batch_size = DEFAULT_BATCH_SIZE
        refresh_ahead = DEFAULT_REFRESH_AHEAD

        if batch_raw:
            try:
                batch_size = max(1, int(batch_raw))
            except ValueError:
                logging.warning(
                    "Invalid TOKEN_MAINTENANCE_BATCH_SIZE value %r; using default %s",
                    batch_raw,
                    DEFAULT_BATCH_SIZE,
                )

        if refresh_raw:
            try:
                refresh_minutes = max(0, int(refresh_raw))
                refresh_ahead = timedelta(minutes=refresh_minutes)
            except ValueError:
                logging.warning(
                    "Invalid TOKEN_MAINTENANCE_REFRESH_AHEAD_MINUTES value %r; using default %s",
                    refresh_raw,
                    DEFAULT_REFRESH_AHEAD,
                )

        job_queue.run_repeating(
            run_token_health_check,
            interval=timedelta(minutes=interval_minutes),
            name="token-health-check",
            data={
                "batch_size": batch_size,
                "refresh_ahead": refresh_ahead,
                "cursor": 0,
            },
        )
        logging.info(
            "🩺 Scheduled token maintenance every %s minute(s) (batch_size=%s, refresh_ahead=%s)",
            interval_minutes,
            batch_size,
            refresh_ahead,
        )

    return application


def main() -> None:
    require_bot_credentials()

    cleanup_report = run_cleanup(full=False)
    cleanup_summary = cleanup_report.summary()
    logging.info(cleanup_summary)
    log_system_info(cleanup_summary)

    threshold_raw = os.getenv("TOKEN_CLEANUP_ALERT_THRESHOLD")
    try:
        alert_threshold = int(threshold_raw) if threshold_raw is not None else 10
    except ValueError:
        logging.warning(
            "Invalid TOKEN_CLEANUP_ALERT_THRESHOLD value %r; defaulting to 10",
            threshold_raw,
        )
        alert_threshold = 10

    alert_threshold = max(0, alert_threshold)
    if alert_threshold and cleanup_report.deleted_count >= alert_threshold:
        alert_message = (
            f"Token cleanup removed {cleanup_report.deleted_count} files during startup "
            f"(mode={cleanup_report.mode})."
        )
        trigger_admin_alert(alert_message)

    application = build_application()
    logging.info("🤖 机器人已成功启动。")
    log_system_info("机器人已成功启动。")
    logging.info("🚀 机器人正在运行。按 Ctrl+C 可停止。")
    logging.info("📡 等待 Telegram 消息中……")
    application.run_polling()


if __name__ == "__main__":
    main()
