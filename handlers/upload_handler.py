import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional, Set

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from creds import get_user_token_path
from exceptions import UploadError
from message_utils import format_download, format_error, format_progress
from monitoring import log_activity, record_upload
from permissions import get_user_role
from plugins import TEXT
from plugins.dpbox import DPBOX
from plugins.wdl import wget_dl
from pySmartDL import SmartDL
from upload import upload as upload_to_drive
from mega import Mega
from google_utils import prepare_user_gauth
from pydrive2.auth import GoogleAuth
UPLOAD_FAIL_PROMPT = format_error("上传失败，请检查授权或网络。")

UploadTask = Dict[str, Any]
UPLOAD_STATUS: Dict[int, UploadTask] = {}
CANCELLED_USERS: Set[int] = set()


def _guess_filename_from_url(url: str) -> str:
    tail = url.rsplit("/", 1)[-1] if "/" in url else url
    tail = tail.split("?")[0]
    return tail or "未命名文件"


def get_user_status(user_id: Optional[int]) -> Optional[UploadTask]:
    if user_id is None:
        return None
    return UPLOAD_STATUS.get(user_id)


def clear_user_status(user_id: Optional[int]) -> None:
    if user_id is None:
        return
    UPLOAD_STATUS.pop(user_id, None)


def clear_cancelled(user_id: Optional[int]) -> None:
    if user_id is None:
        return
    CANCELLED_USERS.discard(user_id)


def is_cancelled(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return user_id in CANCELLED_USERS


def request_cancel(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    if user_id not in UPLOAD_STATUS:
        return False
    CANCELLED_USERS.add(user_id)
    current = UPLOAD_STATUS.get(user_id, {})
    progress = int(current.get("progress", 0))
    filename = current.get("filename") or "未命名文件"
    _update_status(
        user_id,
        stage="任务已取消，正在停止",
        progress=progress,
        filename=filename,
    )
    logging.info("🛑 收到用户ID %s 的取消请求", user_id)
    return True


def _ensure_not_cancelled(user_id: int) -> None:
    if is_cancelled(user_id):
        raise UploadError("任务被用户中断。")


def _update_status(user_id: int, **kwargs: Any) -> None:
    task = UPLOAD_STATUS.setdefault(user_id, {})
    task.update(kwargs)
    task["updated_at"] = datetime.utcnow()


def update_status(user_id: int, **kwargs: Any) -> None:
    _update_status(user_id, **kwargs)


def _remove_local_file(path: Optional[str]) -> None:
    if not path:
        return
    if os.path.exists(path):
        try:
            os.remove(path)
            logging.info("🧹 已删除临时文件：%s", path)
        except Exception as cleanup_error:
            logging.warning("⚠️ 删除临时文件失败：%s", cleanup_error)


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.chat_id is None:
        return

    user_id = update.effective_user.id if update.effective_user else update.message.chat_id
    user_role = get_user_role(user_id)

    token_file_path = str(get_user_token_path(user_id))
    gauth, token_corrupt = prepare_user_gauth(user_id, token_file_path)

    if gauth is None:
        logging.warning(
            "⚠️ 用户 ID %s 缺少有效授权，corrupt=%s。", user_id, token_corrupt
        )
        log_activity(
            user_id or 0,
            user_role,
            "auth_missing",
            source="handlers.upload",
            verification="token_corrupt" if token_corrupt else "token_missing",
            metadata={"corrupt": token_corrupt},
        )
        if token_corrupt:
            prompt_text = (
                f"❌ 用户 ID {user_id} 的授权凭证已失效并被清理，请发送 /auth 重新授权。"
            )
        else:
            prompt_text = (
                f"❌ 用户 ID {user_id} 尚未完成授权，请先发送 /auth 完成授权。"
            )
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=prompt_text,
        )
        return

    url_text = update.message.text or ""
    url = url_text.split()[-1]

    filename_hint = _guess_filename_from_url(url)
    _update_status(user_id, stage="任务已创建，准备下载", progress=5, filename=filename_hint)

    sent_message = await context.bot.send_message(
        chat_id=update.message.chat_id,
        text=format_progress("已收到下载任务，正在排队", 5, f"文件：{filename_hint}"),
        parse_mode=ParseMode.HTML,
    )
    log_activity(
        user_id or 0,
        user_role,
        "receive_url",
        source="handlers.upload",
        metadata={"url": url},
    )

    try:
        _ensure_not_cancelled(user_id)
        await _process_upload(
            url,
            update,
            context,
            sent_message,
            user_id,
            user_role,
            token_file_path,
            gauth,
        )
    except UploadError as error:
        if is_cancelled(user_id):
            logging.info("🛑 用户ID %s 手动中断上传：%s", user_id, error)
            await sent_message.edit_text("🛑 上传任务已终止。")
        else:
            logging.error("❌ 上传失败：%s", error, exc_info=True)
            log_activity(
                user_id or 0,
                user_role,
                "upload_failed",
                source="handlers.upload",
                metadata={"error": str(error)},
            )
            await sent_message.edit_text(UPLOAD_FAIL_PROMPT, parse_mode=ParseMode.HTML)
    except Exception as error:
        logging.exception("❌ 上传流程出现未捕获的异常：%s", error)
        log_activity(
            user_id or 0,
            user_role,
            "upload_exception",
            source="handlers.upload",
            metadata={"error": str(error)},
        )
        await sent_message.edit_text(format_error("系统出现异常，请稍后再试。"), parse_mode=ParseMode.HTML)
    finally:
        clear_user_status(user_id)
        clear_cancelled(user_id)


async def _process_upload(
    url: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    sent_message,
    user_id: int,
    user_role: str,
    token_file_path: str,
    gauth: GoogleAuth,
) -> None:
    filename: Optional[str] = None
    display_name: Optional[str] = None

    filename_hint = _guess_filename_from_url(url)
    _update_status(user_id, stage="正在解析链接", progress=10, filename=filename_hint)
    await sent_message.edit_text(
        format_progress("正在解析链接", 10, f"文件：{filename_hint}"),
        parse_mode=ParseMode.HTML,
    )

    try:
        if "openload" in url or "oload" in url:
            _update_status(user_id, stage="Openload 已下线，无法处理", progress=0)
            await sent_message.edit_text(format_error("Openload 服务已下线，无法处理该链接。"))
            raise UploadError("Openload 已不受支持。")

        if "dropbox.com" in url:
            url = DPBOX(url)
            candidate_name = _guess_filename_from_url(url)
            _update_status(user_id, stage="正在下载 Dropbox 文件", progress=20, filename=candidate_name)
            await sent_message.edit_text(
                format_download("正在下载 Dropbox 文件，请稍候…"),
                parse_mode=ParseMode.HTML,
            )
            _ensure_not_cancelled(user_id)
            filename = await asyncio.to_thread(wget_dl, str(url))
            display_name = os.path.basename(filename)
            logging.info("📥 Dropbox 文件下载完成：%s", display_name)
        elif "mega.nz" in url:
            _update_status(user_id, stage="正在下载 Mega 文件", progress=20)
            try:
                await sent_message.edit_text(
                    format_download("正在下载 Mega 文件，可能略慢，请耐心等待…"),
                    parse_mode=ParseMode.HTML,
                )
                _ensure_not_cancelled(user_id)

                def _download_mega() -> str:
                    mega_client = Mega.from_credentials(
                        TEXT.MEGA_EMAIL,
                        TEXT.MEGA_PASSWORD,
                    )
                    return mega_client.download_from_url(url)

                filename = await asyncio.to_thread(_download_mega)
                display_name = os.path.basename(filename)
                logging.info("📥 Mega 文件下载完成：%s", display_name)
            except Exception as error:
                _update_status(user_id, stage="Mega 下载失败", progress=25)
                raise UploadError("Mega 下载失败。") from error
        else:
            candidate_name = _guess_filename_from_url(url)
            _update_status(user_id, stage="正在下载文件", progress=20, filename=candidate_name)
            await sent_message.edit_text(
                format_download("正在下载文件，请耐心等待…"),
                parse_mode=ParseMode.HTML,
            )
            try:
                _ensure_not_cancelled(user_id)
                filename = await asyncio.to_thread(wget_dl, str(url))
                display_name = os.path.basename(filename)
                logging.info("📥 文件下载完成：%s", display_name)
            except Exception as error:
                if TEXT.DOWN_TWO:
                    logging.warning("⚠️ 下载器 1 出现异常：%s，尝试备用下载器", error)
                    await sent_message.edit_text(
                        format_download("主下载器出现问题，备用下载器正在尝试…"),
                        parse_mode=ParseMode.HTML,
                    )
                    _update_status(user_id, stage="备用下载器正在下载", progress=30)
                    _ensure_not_cancelled(user_id)

                    def _smartdl(download_url: str) -> str:
                        obj = SmartDL(download_url)
                        obj.start()
                        return obj.get_dest()

                    try:
                        filename = await asyncio.to_thread(_smartdl, url)
                        display_name = os.path.basename(filename)
                    except Exception as fallback_error:
                        _update_status(user_id, stage="备用下载器下载失败", progress=35)
                        raise UploadError("备用下载器下载失败。") from fallback_error
                else:
                    _update_status(user_id, stage="下载失败", progress=25)
                    raise UploadError("主下载器下载失败。") from error

        _ensure_not_cancelled(user_id)

        if not filename:
            _update_status(user_id, stage="下载失败，未获得文件名", progress=25)
            raise UploadError("未获取到有效的文件名。")

        if "error" in os.path.basename(filename).lower():
            _update_status(user_id, stage="下载失败，文件损坏", progress=25, filename=filename)
            raise UploadError("下载失败，文件名包含错误标记。")

        display_label = display_name or os.path.basename(filename)
        await sent_message.edit_text(
            format_progress("下载完成，准备上传", 60, f"文件：{display_label}"),
            parse_mode=ParseMode.HTML,
        )
        _update_status(user_id, stage="下载完成，准备上传", progress=60, filename=display_label)

        size_mb = round(os.path.getsize(filename) / 1048576)
        file_display_name = os.path.basename(filename)
        await sent_message.edit_text(
            format_progress("正在上传到 Google Drive", 85, f"文件：{file_display_name}"),
            parse_mode=ParseMode.HTML,
        )
        _update_status(user_id, stage="正在上传到 Google Drive", progress=85, filename=file_display_name)
        _ensure_not_cancelled(user_id)

        try:
            file_link = await asyncio.to_thread(
                upload_to_drive,
                filename,
                update,
                context,
                TEXT.drive_folder_name,
                token_file_path=token_file_path,
                gauth=gauth,
                user_id=user_id,
            )
        except UploadError:
            _update_status(
                user_id, stage="上传至 Google Drive 失败", progress=90, filename=file_display_name
            )
            raise
        except Exception as error:
            _update_status(user_id, stage="上传至 Google Drive 失败", progress=90, filename=file_display_name)
            raise UploadError("Google Drive 上传阶段出现错误。") from error

        _ensure_not_cancelled(user_id)

        _update_status(user_id, stage="上传完成", progress=100, filename=file_display_name)
        await sent_message.edit_text(
            TEXT.DOWNLOAD_URL.format(file_display_name, size_mb, file_link),
            parse_mode=ParseMode.HTML,
        )
        record_upload(user_id or 0, user_role, size_mb, file_display_name)
        log_activity(
            user_id or 0,
            user_role,
            "upload_success",
            source="handlers.upload",
            metadata={
                "file": file_display_name,
                "size_mb": size_mb,
                "link": file_link,
            },
        )

    finally:
        _remove_local_file(filename)
