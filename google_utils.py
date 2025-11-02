from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional, Tuple

from pydrive2.auth import GoogleAuth

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SCOPES: Iterable[str] = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
)

logger = logging.getLogger(__name__)


def ensure_token_storage(token_file: str | Path) -> None:
    """Create the directory that will store OAuth tokens if it does not exist."""
    token_path = Path(token_file).expanduser()
    token_dir = token_path.parent
    if token_dir and not token_dir.exists():
        token_dir.mkdir(parents=True, exist_ok=True)


def configure_gauth(gauth: GoogleAuth, token_file: str | Path) -> GoogleAuth:
    """
    Apply consistent settings to a GoogleAuth instance so that tokens persist
    and refresh tokens are requested explicitly.
    """
    ensure_token_storage(token_file)

    settings = gauth.settings
    client_secrets_path = Path(
        os.getenv(
            "GOOGLE_CLIENT_SECRETS_FILE",
            PROJECT_ROOT / "client_secrets.json",
        )
    )

    settings["client_config_backend"] = "file"
    settings["client_config_file"] = str(client_secrets_path)
    settings["oauth_scope"] = list(DEFAULT_SCOPES)

    settings["save_credentials"] = True
    settings["save_credentials_backend"] = "file"
    token_path = Path(token_file).expanduser()
    settings["save_credentials_file"] = str(token_path)
    settings["save_credentials_dir"] = str(token_path.parent)
    settings["get_refresh_token"] = True

    auth_param = settings.get("auth_param", {}) or {}
    auth_param["access_type"] = "offline"
    auth_param["prompt"] = "consent"
    settings["auth_param"] = auth_param

    return gauth


def prepare_user_gauth(
    user_id: int, token_file: str | Path
) -> Tuple[Optional[GoogleAuth], bool]:
    """Return a configured GoogleAuth for the user if credentials are valid.

    Args:
        user_id: Telegram user ID used for logging.
        token_file: Path to the per-user credential file.

    Returns:
        tuple[GoogleAuth | None, bool]: The prepared GoogleAuth instance or ``None``
            if credentials are missing/invalid, and a boolean indicating whether the
            token file appeared corrupt and was removed.
    """

    token_path = Path(token_file).expanduser()
    gauth = configure_gauth(GoogleAuth(), token_path)
    ensure_token_storage(token_path)

    try:
        gauth.LoadCredentialsFile(str(token_path))
    except Exception as load_error:
        logger.warning(
            "⚠️ 无法加载用户 %s 的凭证文件：%s", user_id, load_error, exc_info=True
        )
        if token_path.exists():
            try:
                token_path.unlink()
                logger.info(
                    "🧹 已删除用户 %s 的损坏凭证文件：%s", user_id, token_path
                )
            except Exception as cleanup_error:  # pragma: no cover - defensive
                logger.warning(
                    "⚠️ 删除用户 %s 的损坏凭证文件失败：%s",
                    user_id,
                    cleanup_error,
                    exc_info=True,
                )
        return None, True

    credentials = getattr(gauth, "credentials", None)
    if credentials is None:
        return None, False

    if getattr(credentials, "invalid", False):
        if token_path.exists():
            try:
                token_path.unlink()
                logger.info(
                    "🧹 已清理用户 %s 的无效凭证文件：%s", user_id, token_path
                )
            except Exception as cleanup_error:  # pragma: no cover - defensive
                logger.warning(
                    "⚠️ 删除用户 %s 的无效凭证文件失败：%s",
                    user_id,
                    cleanup_error,
                    exc_info=True,
                )
        return None, True

    return gauth, False
