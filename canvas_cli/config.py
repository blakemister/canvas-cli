"""Configuration for Canvas CLI.

Config values resolve in this order (first match wins):
  1. Environment variable
  2. Persistent config file at ~/.canvas-cli/config.json
  3. Built-in default (where one exists)

Managed via `canvas config set <key> <value>` and `canvas config show`.

Keys:
- base_url (env: CANVAS_BASE_URL) — required, e.g. https://canvas.instructure.com
- cookie_file (env: CANVAS_COOKIE_FILE) — default ~/.canvas-cli/cookies.json
- token_refresh_script (env: CANVAS_TOKEN_REFRESH_SCRIPT) — optional
- upload_size_limit_mb (env: CANVAS_UPLOAD_SIZE_LIMIT_MB) — default 100
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".canvas-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_COOKIE_FILE = CONFIG_DIR / "cookies.json"

_VALID_KEYS = {"base_url", "cookie_file", "token_refresh_script", "upload_size_limit_mb"}


def _load_config_file() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _resolve(env_name: str, file_key: str, default=None):
    """Resolve a config value: env var → config file → default."""
    env_val = os.environ.get(env_name)
    if env_val:
        return env_val
    file_val = _load_config_file().get(file_key)
    if file_val:
        return file_val
    return default


def save_config(key: str, value: str) -> None:
    """Write a single key/value to the persistent config file."""
    if key not in _VALID_KEYS:
        raise ValueError(f"Unknown config key: {key}. Valid keys: {sorted(_VALID_KEYS)}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = _load_config_file()
    data[key] = value
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def show_config() -> dict:
    """Return the effective config (env + file merged) for display."""
    return {
        "base_url": _resolve("CANVAS_BASE_URL", "base_url"),
        "cookie_file": _resolve("CANVAS_COOKIE_FILE", "cookie_file", str(DEFAULT_COOKIE_FILE)),
        "token_refresh_script": _resolve("CANVAS_TOKEN_REFRESH_SCRIPT", "token_refresh_script"),
        "upload_size_limit_mb": _resolve("CANVAS_UPLOAD_SIZE_LIMIT_MB", "upload_size_limit_mb", "100"),
        "config_file": str(CONFIG_FILE) if CONFIG_FILE.exists() else "(none)",
    }


# --- Resolved values (computed at import time) ---

_raw_url = (_resolve("CANVAS_BASE_URL", "base_url") or "").rstrip("/")
CANVAS_URL = _raw_url
API_BASE = f"{_raw_url}/api/v1" if _raw_url else ""

COOKIE_FILE = Path(_resolve("CANVAS_COOKIE_FILE", "cookie_file", str(DEFAULT_COOKIE_FILE)))

_token_script = _resolve("CANVAS_TOKEN_REFRESH_SCRIPT", "token_refresh_script")
GET_TOKEN_SCRIPT = Path(_token_script) if _token_script else None

COURSE_CACHE_FILE = CONFIG_DIR / "courses.json"
RECEIPTS_DIR = CONFIG_DIR / "submissions"

UPLOAD_SIZE_LIMIT_BYTES = int(
    _resolve("CANVAS_UPLOAD_SIZE_LIMIT_MB", "upload_size_limit_mb", "100")
) * 1024 * 1024
PER_PAGE = 100
MAX_RETRIES = 3
INITIAL_BACKOFF = 2


def require_base_url() -> None:
    """Raise a clear error if base_url isn't configured."""
    if not CANVAS_URL:
        raise RuntimeError(
            "base_url is not configured. Run one of:\n"
            "  canvas config set base_url https://your-school.instructure.com\n"
            "or set the CANVAS_BASE_URL environment variable."
        )
