import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID") or None


def get_google_token_base_dir(default: str | Path = "user_data") -> Path:
    """Return the directory used to store Google credential tokens."""

    return Path(os.getenv("GOOGLE_TOKEN_DIR", default)).expanduser()


GOOGLE_TOKEN_BASE_DIR = get_google_token_base_dir()


def get_user_token_path(user_id: int) -> Path:
    return GOOGLE_TOKEN_BASE_DIR / f"token_{user_id}.json"


def _resolve_default_token_path() -> Path:
    token_file_env = os.getenv("GOOGLE_TOKEN_FILE")
    if token_file_env:
        return Path(token_file_env).expanduser()
    return GOOGLE_TOKEN_BASE_DIR / "token.json"


GOOGLE_TOKEN_FILE = str(_resolve_default_token_path().resolve())
ENABLE_FORWARD_INFO = _env_bool("ENABLE_FORWARD_INFO", True)
CACHE_DIRECTORY = os.getenv("CACHE_DIR", "Downloads")


def require_bot_credentials() -> None:
    """Ensure the mandatory bot credentials are present."""

    required_envs = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
    }
    missing = [name for name, value in required_envs.items() if not value]
    if missing:
        joined = ", ".join(missing)
        raise EnvironmentError(f"缺少必要的环境变量：{joined}")
