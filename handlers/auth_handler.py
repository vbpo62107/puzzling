import logging
import os
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from creds import get_user_token_path
from exceptions import AuthError
from google_utils import configure_gauth, ensure_token_storage
from plugins import TEXT
from plugins.tok_rec import is_token
from pydrive2.auth import GoogleAuth

AUTH_FAIL_PROMPT = "❌ 授权失败，请检查凭证或网络。"


def _resolve_token_path(update: Update) -> str:
    user_id: Optional[int] = None
    if update.effective_user and update.effective_user.id is not None:
        user_id = update.effective_user.id
    elif update.effective_chat and update.effective_chat.id is not None:
        user_id = update.effective_chat.id

    if user_id is None:
        raise AuthError("无法确定用户或会话 ID。")

    return str(get_user_token_path(user_id))


async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        token_file_path = _resolve_token_path(update)
        gauth = configure_gauth(GoogleAuth(), token_file_path)
        ensure_token_storage(token_file_path)
        try:
            gauth.LoadCredentialsFile(token_file_path)
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
                ensure_token_storage(token_file_path)
                gauth.SaveCredentialsFile(token_file_path)
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
        token_file_path = _resolve_token_path(update)
        gauth = configure_gauth(GoogleAuth(), token_file_path)
        ensure_token_storage(token_file_path)
        try:
            gauth.Auth(auth_code)
        except Exception as verify_error:
            raise AuthError("验证授权凭证失败。") from verify_error

        try:
            gauth.SaveCredentialsFile(token_file_path)
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
        token_file_path = _resolve_token_path(update)
        if os.path.exists(token_file_path):
            os.remove(token_file_path)
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
