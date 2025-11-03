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
from permissions import mark_token_absent, mark_token_present

AUTH_FAIL_PROMPT = "❌ 授权失败，请检查凭证或网络。"


def _resolve_user_id(update: Update) -> int:
    user_id: Optional[int] = None
    if update.effective_user and update.effective_user.id is not None:
        user_id = update.effective_user.id
    elif update.effective_chat and update.effective_chat.id is not None:
        user_id = update.effective_chat.id

    if user_id is None:
        raise AuthError("无法确定用户或会话 ID。")

    return user_id


async def _prompt_reauthorization(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    gauth: GoogleAuth,
) -> None:
    if not update.effective_chat:
        return

    try:
        auth_url = gauth.GetAuthUrl()
    except Exception as auth_url_error:  # pragma: no cover - defensive logging
        logging.exception("❌ 无法生成重新授权链接：%s", auth_url_error)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=AUTH_FAIL_PROMPT,
        )
        return

    message = TEXT.AUTH_URL.format(auth_url)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        parse_mode=ParseMode.HTML,
    )


async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = _resolve_user_id(update)
        token_file_path = str(get_user_token_path(user_id))
        gauth = configure_gauth(GoogleAuth(), token_file_path)
        ensure_token_storage(token_file_path)
        try:
            gauth.LoadCredentialsFile(token_file_path)
        except Exception as load_error:
            logging.warning(
                "⚠️ 用户 %s 的凭证文件无法加载：%s", user_id, load_error, exc_info=True
            )
            await _prompt_reauthorization(update, context, gauth)
            return

        if gauth.credentials is None:
            logging.info("ℹ️ 用户 %s 尚未授权，发送授权链接。", user_id)
            await _prompt_reauthorization(update, context, gauth)
            return

        if gauth.access_token_expired:
            try:
                gauth.Refresh()
                ensure_token_storage(token_file_path)
                gauth.SaveCredentialsFile(token_file_path)
                logging.info("🔄 已为用户 %s 刷新访问令牌。", user_id)
                if update.effective_chat:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=TEXT.ALREADY_AUTH,
                    )
            except Exception as refresh_error:
                logging.error(
                    "❌ 刷新用户 %s 的授权凭证失败：%s",
                    user_id,
                    refresh_error,
                    exc_info=True,
                )
                await _prompt_reauthorization(update, context, gauth)
            return

        try:
            gauth.Authorize()
        except Exception as authorize_error:
            logging.error(
                "❌ 用户 %s 的凭证无法授权：%s",
                user_id,
                authorize_error,
                exc_info=True,
            )
            await _prompt_reauthorization(update, context, gauth)
            return

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

    try:
        user_id = _resolve_user_id(update)
    except AuthError as auth_error:
        logging.error("❌ 无法解析用户 ID：%s", auth_error, exc_info=True)
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=AUTH_FAIL_PROMPT,
        )
        return

    msg = update.message.text or ""
    if not is_token(msg):
        return

    auth_code = msg.split()[-1]
    logging.info("收到用户 %s 的新授权令牌请求，正在尝试验证…", user_id)
    try:
        token_file_path = str(get_user_token_path(user_id))
        gauth = configure_gauth(GoogleAuth(), token_file_path)
        ensure_token_storage(token_file_path)
        try:
            gauth.LoadCredentialsFile(token_file_path)
        except Exception as load_error:
            logging.warning(
                "⚠️ 在为用户 %s 保存新凭证前加载旧凭证失败：%s",
                user_id,
                load_error,
                exc_info=True,
            )

        try:
            gauth.Auth(auth_code)
        except Exception as verify_error:
            raise AuthError("验证授权凭证失败。") from verify_error

        try:
            gauth.SaveCredentialsFile(token_file_path)
        except Exception as save_error:
            raise AuthError("保存授权凭证失败。") from save_error

        logging.info("✅ 用户 %s 的授权令牌保存成功。", user_id)
        mark_token_present(user_id)
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
        user_id = _resolve_user_id(update)
        token_file_path = str(get_user_token_path(user_id))
        if os.path.exists(token_file_path):
            os.remove(token_file_path)
            mark_token_absent(user_id)
            logging.info("🔒 已撤销用户 %s 的本地凭证文件。", user_id)
            if update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=TEXT.REVOKE_TOK,
                )
        else:
            logging.warning("⚠️ 用户 %s 未找到可撤销的凭证文件。", user_id)
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
