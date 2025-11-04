#!/usr/bin/env python3
import logging
import os
import os.path as path
from pathlib import Path
from typing import Optional, Tuple

from googleapiclient.errors import HttpError
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

from creds import GOOGLE_DRIVE_FOLDER_ID
from google_utils import (
    TokenState,
    configure_gauth,
    ensure_token_storage,
    refresh_user_gauth,
)
from security.token_store import token_store
from exceptions import UploadError

logger = logging.getLogger(__name__)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
def _resolve_destination_folder(
    drive: GoogleDrive,
    default_folder_id: Optional[str],
    parent_folder: Optional[str],
) -> Tuple[Optional[str], bool]:
    """
    Determine the Drive folder to receive the upload.

    Returns:
        tuple[str | None, bool]: (folder_id, is_env_folder)
            folder_id: Target folder ID, or None for root uploads.
            is_env_folder: True if the ID comes from GOOGLE_DRIVE_FOLDER_ID.
    """
    folder_id = (default_folder_id or "").strip()

    if folder_id:
        try:
            folder_meta = drive.CreateFile({"id": folder_id})
            folder_meta.FetchMetadata(fields="id, mimeType")
            if folder_meta.get("mimeType") == FOLDER_MIME_TYPE:
                return folder_id, True
            logger.warning(
                "⚠️ 提供的 GOOGLE_DRIVE_FOLDER_ID (%s) 不是文件夹，将改为自动创建模式。",
                folder_id,
            )
        except HttpError as err:
            status = getattr(getattr(err, "resp", None), "status", None)
            if status == 404:
                logger.warning(
                    "⚠️ 指定的 GOOGLE_DRIVE_FOLDER_ID (%s) 未找到，使用自动创建的文件夹。",
                    folder_id,
                )
            else:
                raise
        folder_id = ""

    if parent_folder:
        query = (
            f"'root' in parents and trashed=false and mimeType='{FOLDER_MIME_TYPE}'"
        )
        try:
            file_list = drive.ListFile({"q": query}).GetList()
        except HttpError as err:
            logger.error("❌ 无法列出云端文件夹：%s", err)
            raise

        for file_folder in file_list:
            if file_folder.get("title") == parent_folder:
                logger.info("📂 云端已存在目标文件夹，直接使用：%s", parent_folder)
                return file_folder.get("id"), False

        folder_metadata = {"title": parent_folder, "mimeType": FOLDER_MIME_TYPE}
        folder = drive.CreateFile(folder_metadata)
        folder.Upload()
        logger.info(
            "📂 已创建新的云端文件夹：%s (ID: %s)",
            folder.get("title"),
            folder.get("id"),
        )
        return folder.get("id"), False

    return None, False


def _quarantine_token(user_id: Optional[int], reason: str, fallback_path: str) -> None:
    if user_id is not None:
        try:
            token_store().quarantine(user_id, reason)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "⚠️ 隔离用户 %s 的凭证失败：%s", user_id, exc, exc_info=True
            )
        return

    token_path = Path(fallback_path).expanduser()
    if not token_path.exists():
        return
    try:
        token_path.unlink()
        logger.info("🧹 已删除损坏的凭证文件：%s", token_path)
    except Exception as cleanup_error:  # pragma: no cover - defensive logging
        logger.warning(
            "⚠️ 删除损坏的凭证文件失败：%s", cleanup_error, exc_info=True
        )


def upload(
    filename: str,
    update,
    context,
    parent_folder: str = None,
    *,
    token_file_path: str,
    gauth: Optional[GoogleAuth] = None,
    user_id: Optional[int] = None,
) -> str:
    logger.info(
        "☁️ 即将为用户 %s 上传文件：%s",
        user_id if user_id is not None else "未知",
        filename,
    )

    resolved_path = Path(token_file_path).expanduser()
    if user_id is not None:
        resolved_path = token_store().get_token_path(user_id)

    ensure_token_storage(resolved_path)
    gauth = configure_gauth(gauth or GoogleAuth(), resolved_path)

    if getattr(gauth, "credentials", None) is None:
        try:
            gauth.LoadCredentialsFile(str(resolved_path))
        except Exception as load_error:
            logger.error(
                "❌ 无法加载用户 %s 的授权凭证：%s",
                user_id,
                load_error,
                exc_info=True,
            )
            _quarantine_token(user_id, "load_error", str(resolved_path))
            raise UploadError(
                f"用户 {user_id or '未知'} 的授权凭证缺失或已损坏，请发送 /auth 重新授权。"
            ) from load_error

    if gauth.credentials is None:
        raise UploadError(
            f"用户 {user_id or '未知'} 尚未授权，请发送 /auth 完成授权。"
        )

    if getattr(gauth.credentials, "invalid", False):
        logger.warning("⚠️ 用户 %s 的凭证标记为无效。", user_id)
        _quarantine_token(user_id, "invalid_credentials", str(resolved_path))
        raise UploadError(
            f"用户 {user_id or '未知'} 的授权已失效，请发送 /auth 重新授权。"
        )

    if gauth.access_token_expired:
        if user_id is None:
            raise UploadError("缺少用户信息，无法刷新授权凭证。")
        refresh_result = refresh_user_gauth(user_id, gauth)
        gauth = refresh_result.gauth
        if refresh_result.state is not TokenState.VALID or gauth is None:
            logger.error(
                "❌ 刷新用户 %s 的授权凭证失败：%s",
                user_id,
                refresh_result.error,
            )
            _quarantine_token(user_id, "refresh_failed", str(resolved_path))
            raise UploadError(
                f"用户 {user_id or '未知'} 的授权凭证无法刷新，请重新发送 /auth。"
            )
        logger.info("🔄 已刷新用户 %s 的访问令牌。", user_id)

    try:
        gauth.Authorize()
    except Exception as authorize_error:
        logger.error(
            "❌ 授权用户 %s 的凭证失败：%s",
            user_id,
            authorize_error,
            exc_info=True,
        )
        _quarantine_token(user_id, "authorize_failed", str(resolved_path))
        raise UploadError(
            f"用户 {user_id or '未知'} 的授权验证失败，请重新执行 /auth。"
        ) from authorize_error

    drive = GoogleDrive(gauth)
    http = drive.auth.Get_Http_Object()

    if not path.exists(filename):
        logger.error("❌ 指定的文件不存在：%s", filename)
        raise FileNotFoundError(filename)

    try:
        target_folder_id, used_env_folder = _resolve_destination_folder(
            drive, GOOGLE_DRIVE_FOLDER_ID, parent_folder
        )
    except HttpError as err:
        logger.error("❌ 验证目标文件夹时发生错误：%s", err)
        raise

    file_params = {"title": os.path.basename(filename)}
    if target_folder_id:
        file_params["parents"] = [
            {"kind": "drive#fileLink", "id": target_folder_id}
        ]

    file_to_upload = drive.CreateFile(file_params)
    file_to_upload.SetContentFile(filename)

    upload_params = {"http": http, "supportsAllDrives": True}

    try:
        file_to_upload.Upload(param=upload_params)
    except Exception as err:
        logger.error("❌ 上传文件时出错：%s", err)
        raise

    if not used_env_folder:
        file_to_upload.FetchMetadata()
        file_to_upload.InsertPermission(
            {
                "type": "anyone",
                "value": "anyone",
                "role": "reader",
                "withLink": True,
            }
        )

    return file_to_upload.get("webContentLink")
