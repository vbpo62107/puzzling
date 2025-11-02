from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from pydrive2.auth import GoogleAuth

from creds import GOOGLE_TOKEN_FILE

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SCOPES: Iterable[str] = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
)


def ensure_token_storage() -> None:
    """Create the directory that will store OAuth tokens if it does not exist."""
    token_path = Path(GOOGLE_TOKEN_FILE).expanduser()
    token_dir = token_path.parent
    if token_dir and not token_dir.exists():
        token_dir.mkdir(parents=True, exist_ok=True)


def configure_gauth(gauth: GoogleAuth) -> GoogleAuth:
    """
    Apply consistent settings to a GoogleAuth instance so that tokens persist
    and refresh tokens are requested explicitly.
    """
    ensure_token_storage()

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
    settings["save_credentials_file"] = str(Path(GOOGLE_TOKEN_FILE).expanduser())
    settings["save_credentials_dir"] = str(Path(GOOGLE_TOKEN_FILE).expanduser().parent)
    settings["get_refresh_token"] = True

    auth_param = settings.get("auth_param", {}) or {}
    auth_param["access_type"] = "offline"
    auth_param["prompt"] = "consent"
    settings["auth_param"] = auth_param

    return gauth
