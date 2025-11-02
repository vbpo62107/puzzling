import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from creds import GOOGLE_TOKEN_FILE
from exceptions import AuthError
from google_utils import configure_gauth, ensure_token_storage
from plugins import TEXT
from plugins.tok_rec import is_token
from pydrive2.auth import GoogleAuth

gauth = configure_gauth(GoogleAuth())
TOKEN_FILE_PATH = GOOGLE_TOKEN_FILE
AUTH_FAIL_PROMPT = "❌ 授权失败，请检查凭证或网络。"


async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        ensure_token_storage()
        try:
            gauth.LoadCredentialsFile(TOKEN_FILE_PATH)
        except Exception as load_error:
            logging.warning("⚠️ 未找到凭证文件：%s", load_error)

        if gauth.credentials is None:
            auth_url = gauth.GetAuthUrl()
            message = TEXT.AUTH_URL.format(auth_url)
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                )
        elif gauth.access_token_expired:
            try:
                gauth.Refresh()
                ensure_token_storage()
                gauth.SaveCredentialsFile(TOKEN_FILE_PATH)
            except Exception as refresh_error:
                raise AuthError("刷新授权凭证失败。") from refresh_error
        else:
            try:
                gauth.Authorize()
            except Exception as authorize_error:
                raise AuthError("凭证文件无法授权。") from authorize_error
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=TEXT.ALREADY_AUTH,
                )
    except AuthError as auth_error:
        logging.error("❌ 授权流程失败：%s", auth_error, exc_info=True)
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=AUTH_FAIL_PROMPT,
            )
    except Exception as error:
        logging.exception("❌ 授权流程出现未预期异常：%s", error)
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=AUTH_FAIL_PROMPT,
            )


async def token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    msg = update.message.text or ""
    if not is_token(msg):
        return

    auth_code = msg.split()[-1]
    logging.info("收到新的授权令牌请求，正在尝试验证…")
    try:
        ensure_token_storage()
        try:
            gauth.Auth(auth_code)
        except Exception as verify_error:
            raise AuthError("验证授权令牌失败。") from verify_error

        try:
            gauth.SaveCredentialsFile(TOKEN_FILE_PATH)
        except Exception as save_error:
            raise AuthError("保存授权凭证失败。") from save_error

        logging.info("✅ 授权令牌保存成功。")
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=TEXT.AUTH_SUCC,
        )
    except AuthError as auth_error:
        logging.error("❌ 授权失败：%s", auth_error, exc_info=True)
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=TEXT.AUTH_ERROR,
        )
    except Exception as error:
        logging.exception("❌ 授权流程出现未预期异常：%s", error)
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=AUTH_FAIL_PROMPT,
        )


async def revoke_tok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if os.path.exists(TOKEN_FILE_PATH):
            os.remove(TOKEN_FILE_PATH)
            logging.info("🔒 已撤销本地凭证文件。")
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=TEXT.REVOKE_TOK,
                )
        else:
            logging.warning("⚠️ 未找到可撤销的凭证文件。")
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=TEXT.REVOKE_FAIL,
                )
    except Exception as error:
        logging.exception("❌ 撤销凭证时发生异常：%s", error)
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=TEXT.REVOKE_FAIL,
            )
