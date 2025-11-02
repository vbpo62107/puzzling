#!/usr/bin/env python3
import logging
import os
import os.path as path
from typing import Optional, Tuple

from googleapiclient.errors import HttpError
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

from creds import GOOGLE_DRIVE_FOLDER_ID, GOOGLE_TOKEN_FILE
from google_utils import configure_gauth, ensure_token_storage

logger = logging.getLogger(__name__)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
TOKEN_FILE_PATH = GOOGLE_TOKEN_FILE


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


def upload(filename: str, update, context, parent_folder: str = None) -> str:
    gauth: GoogleAuth = configure_gauth(GoogleAuth())
    ensure_token_storage()
    gauth.LoadCredentialsFile(TOKEN_FILE_PATH)

    if gauth.credentials is None:
        logger.warning("⚠️ 尚未完成授权流程。")
    elif gauth.access_token_expired:
        gauth.Refresh()
        ensure_token_storage()
        gauth.SaveCredentialsFile(TOKEN_FILE_PATH)
    else:
        gauth.Authorize()

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
