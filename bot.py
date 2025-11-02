#!/usr/bin/env python3

import logging
import os

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from creds import TELEGRAM_BOT_TOKEN
from handlers.auth_handler import auth, revoke_tok, token
from handlers.file_handler import handle_file_message
from handlers.status_handler import (
    cancel,
    help as help_command,
    ping,
    start,
    status,
    updates,
)
from handlers.upload_handler import upload
from handlers.admin_handler import add_user, list_users_command, remove_user_command, show_logs
from monitoring import log_system_info, setup_logging

LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()
setup_logging(LOG_LEVEL_NAME)

logging.info("🤖 机器人启动中…")


def build_application():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("auth", auth))
    application.add_handler(CommandHandler("revoke", revoke_tok))
    application.add_handler(CommandHandler("update", updates))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("logs", show_logs))
    application.add_handler(CommandHandler("adduser", add_user))
    application.add_handler(CommandHandler("removeuser", remove_user_command))
    application.add_handler(CommandHandler("users", list_users_command))

    application.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.PHOTO),
            handle_file_message,
        )
    )
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"http"), upload))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"http"), token)
    )

    return application


def main() -> None:
    application = build_application()
    logging.info("🤖 机器人已成功启动。")
    log_system_info("机器人已成功启动。")
    logging.info("🚀 机器人正在运行。按 Ctrl+C 可停止。")
    logging.info("📡 等待 Telegram 消息中……")
    application.run_polling()


if __name__ == "__main__":
    main()
