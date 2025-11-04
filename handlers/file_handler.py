import asyncio
import html
import logging
import os
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from creds import CACHE_DIRECTORY, ENABLE_FORWARD_INFO, get_user_token_path
from exceptions import UploadError
from handlers.upload_handler import (
    UPLOAD_FAIL_PROMPT,
    clear_cancelled,
    clear_user_status,
    is_cancelled,
    update_status,
)
from monitoring import log_activity, record_upload
from permissions import get_user_role
from message_utils import (
    format_download,
    format_error,
    format_progress,
    format_success,
    format_upload,
)
from plugins import TEXT
from upload import upload as upload_to_drive
from google_utils import TokenState, prepare_user_gauth

CACHE_DIR = Path(CACHE_DIRECTORY).expanduser()


def _build_unique_path(base_dir: Path, file_name: str) -> Path:
    safe_name = Path(file_name).name or "file"
    candidate = base_dir / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        candidate = base_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _extract_forward_source(update: Update) -> Optional[str]:
    message = update.message
    if message is None:
        return None

    if message.forward_from:
        username = message.forward_from.username
        if username:
            return f"@{username}"
        full_name = " ".join(
            part for part in [message.forward_from.first_name, message.forward_from.last_name] if part
        )
        return full_name or str(message.forward_from.id)

    if message.forward_from_chat:
        username = message.forward_from_chat.username
        if username:
            return f"@{username}"
        return message.forward_from_chat.title or str(message.forward_from_chat.id)

    if message.forward_sender_name:
        return message.forward_sender_name

    return None


def _raise_if_cancelled(user_id: int) -> None:
    if is_cancelled(user_id):
        raise UploadError("任务被用户中断。")


async def handle_file_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return

    chat_id = message.chat_id
    user_id = update.effective_user.id if update.effective_user else chat_id
    user_role = get_user_role(user_id)

    token_file_path = str(get_user_token_path(user_id))
    token_result = prepare_user_gauth(user_id, token_file_path)
    gauth = token_result.gauth

    if token_result.state is not TokenState.VALID or gauth is None:
        reason_map = {
            TokenState.CORRUPTED: "token_corrupt",
            TokenState.REFRESH_FAILED: "refresh_failed",
            TokenState.ABSENT: "token_absent",
            TokenState.EXPIRED: "token_expired",
        }
        reason = reason_map.get(token_result.state, "token_invalid")
        logging.warning(
            "⚠️ 用户 ID %s 的授权凭证无效，state=%s。",
            user_id,
            token_result.state.value,
        )
        log_activity(
            user_id or 0,
            user_role,
            "auth_missing",
            source="handlers.file",
            verification=reason,
            metadata={**token_result.as_metadata()},
        )
        if token_result.state in {TokenState.CORRUPTED, TokenState.REFRESH_FAILED}:
            prompt_text = (
                f"❌ 用户 ID {user_id} 的授权凭证已失效并被清理，请发送 /auth 重新授权。"
            )
        else:
            prompt_text = "❌ 未能加载您的授权凭证，请重新发送 /auth 完成授权。"
        await context.bot.send_message(chat_id=chat_id, text=prompt_text)
        return

    document = message.document
    photo = message.photo[-1] if message.photo else None

    if document:
        telegram_file = await document.get_file()
        original_name = document.file_name or f"document_{document.file_unique_id}"
    elif photo:
        telegram_file = await photo.get_file()
        original_name = f"photo_{photo.file_unique_id}.jpg"
    else:
        return

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_path = _build_unique_path(CACHE_DIR, original_name)
    display_name = local_path.name

    forward_source = _extract_forward_source(update) if ENABLE_FORWARD_INFO else None

    logging.info("📦 收到来自用户ID %s 的文件：%s", user_id, display_name)
    log_activity(
        user_id or 0,
        user_role,
        "receive_file",
        source="handlers.file",
        metadata={"file": display_name},
    )
    if forward_source:
        logging.info("↪️ 文件转发来源：%s", forward_source)
        log_activity(
            user_id or 0,
            user_role,
            "forward_source",
            source="handlers.file",
            metadata={"forward_source": forward_source},
        )

    update_status(user_id, stage="任务已创建，准备下载", progress=5, filename=display_name)
    status_message = await context.bot.send_message(
        chat_id=chat_id,
        text=format_progress("已收到文件，正在排队处理", 5, f"文件：{display_name}"),
        parse_mode=ParseMode.HTML,
    )

    try:
        _raise_if_cancelled(user_id)

        update_status(user_id, stage="正在下载文件", progress=25, filename=display_name)
        await status_message.edit_text(
            format_progress("正在下载文件", 25, f"文件：{display_name}"),
            parse_mode=ParseMode.HTML,
        )
        await telegram_file.download_to_drive(custom_path=str(local_path))

        _raise_if_cancelled(user_id)

        update_status(user_id, stage="下载完成，准备上传", progress=60, filename=display_name)
        await status_message.edit_text(
            format_progress("下载完成，准备上传", 60, f"文件：{display_name}"),
            parse_mode=ParseMode.HTML,
        )

        size_mb = round(os.path.getsize(local_path) / 1048576)
        update_status(user_id, stage="正在上传到 Google Drive", progress=85, filename=display_name)
        await status_message.edit_text(
            format_progress("正在上传到 Google Drive", 85, f"文件：{display_name}"),
            parse_mode=ParseMode.HTML,
        )

        file_link = await asyncio.to_thread(
            upload_to_drive,
            str(local_path),
            update,
            context,
            TEXT.drive_folder_name,
            token_file_path=token_file_path,
            gauth=gauth,
            user_id=user_id,
        )

        _raise_if_cancelled(user_id)

        update_status(user_id, stage="上传完成", progress=100, filename=display_name)
        response_text = TEXT.DOWNLOAD_URL.format(display_name, size_mb, file_link)
        if forward_source:
            response_text += f"\n来源用户：{html.escape(forward_source)}"

        record_upload(user_id or 0, user_role, size_mb, display_name)
        log_activity(
            user_id or 0,
            user_role,
            "upload_success",
            source="handlers.file",
            metadata={
                "file": display_name,
                "size_mb": size_mb,
                "link": file_link,
                "forward_source": forward_source,
            },
        )
        await status_message.edit_text(response_text, parse_mode=ParseMode.HTML)

    except UploadError as error:
        if is_cancelled(user_id):
            logging.info("🛑 用户ID %s 手动中断上传：%s", user_id, error)
            await status_message.edit_text("🛑 上传任务已终止。")
        else:
            logging.error("❌ 上传失败：%s", error, exc_info=True)
            log_activity(
                user_id or 0,
                user_role,
                "upload_failed",
                source="handlers.file",
                metadata={"error": str(error)},
            )
            await status_message.edit_text(format_error("上传失败，请稍后重试。"))
    except Exception as error:
        logging.exception("❌ 文件上传流程出现未捕获的异常：%s", error)
        await status_message.edit_text(format_error("系统出现异常，请稍后再试。"))
    finally:
        if local_path.exists():
            try:
                local_path.unlink()
                logging.info("🧹 已删除本地缓存文件：%s", local_path)
            except Exception as cleanup_error:
                logging.warning("⚠️ 删除本地缓存文件失败：%s", cleanup_error)

        clear_user_status(user_id)
        clear_cancelled(user_id)
