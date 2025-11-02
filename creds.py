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

token_file_env = os.getenv("GOOGLE_TOKEN_FILE")
if token_file_env:
    token_path = Path(token_file_env).expanduser()
else:
    token_dir = Path(
        os.getenv(
            "GOOGLE_TOKEN_DIR",
            os.path.expanduser("~/.config/google-drive-uploader"),
        )
    )
    token_path = token_dir / "token.json"

GOOGLE_TOKEN_FILE = str(token_path.resolve())
ENABLE_FORWARD_INFO = _env_bool("ENABLE_FORWARD_INFO", True)
CACHE_DIRECTORY = os.getenv("CACHE_DIR", "Downloads")

required_envs = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
}
missing = [name for name, value in required_envs.items() if not value]
if missing:
    joined = ", ".join(missing)
    raise EnvironmentError(f"缺少必要的环境变量：{joined}")
