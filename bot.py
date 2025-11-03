#!/usr/bin/env python3

import logging
import os

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
    remove_user_command,
    show_logs,
)
from monitoring import log_system_info, setup_logging, trigger_admin_alert
from puzzling.token_cleanup import run_cleanup
from security import security_interceptor

LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()
setup_logging(LOG_LEVEL_NAME)

logging.info("🤖 机器人启动中…")


def build_application():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", security_interceptor.wrap("start", start)))
    application.add_handler(CommandHandler("help", security_interceptor.wrap("help", help_command)))
    application.add_handler(CommandHandler("auth", security_interceptor.wrap("auth", auth)))
    application.add_handler(CommandHandler("revoke", security_interceptor.wrap("revoke_tok", revoke_tok)))
    application.add_handler(CommandHandler("update", security_interceptor.wrap("updates", updates)))
    application.add_handler(CommandHandler("mystatus", security_interceptor.wrap("my_status", my_status)))
    application.add_handler(CommandHandler("status", security_interceptor.wrap("status", status)))
    application.add_handler(CommandHandler("cancel", security_interceptor.wrap("cancel", cancel)))
    application.add_handler(CommandHandler("ping", security_interceptor.wrap("ping", ping)))
    application.add_handler(CommandHandler("logs", security_interceptor.wrap("show_logs", show_logs)))
    application.add_handler(CommandHandler("adduser", security_interceptor.wrap("add_user", add_user)))
    application.add_handler(
        CommandHandler("removeuser", security_interceptor.wrap("remove_user_command", remove_user_command))
    )
    application.add_handler(CommandHandler("users", security_interceptor.wrap("list_users_command", list_users_command)))
    application.add_handler(CommandHandler("cleanup", security_interceptor.wrap("cleanup_tokens", cleanup_tokens_command)))

    application.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.PHOTO),
            security_interceptor.wrap("handle_file_message", handle_file_message),
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"http"),
            security_interceptor.wrap("upload", upload),
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"http"),
            security_interceptor.wrap("token", token),
        )
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
