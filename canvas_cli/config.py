"""Configuration for Canvas CLI.

Values are read from environment variables, with sensible defaults where
possible. CANVAS_BASE_URL is required and must point to your Canvas
instance (e.g. https://canvas.instructure.com or https://school.instructure.com).

Optional env vars:
- CANVAS_COOKIE_FILE — path to JSON file with session cookies.
  Default: ~/.canvas-cli/cookies.json
- CANVAS_TOKEN_REFRESH_SCRIPT — absolute path to a script that refreshes
  cookies on 401 (e.g. your own Playwright-based SSO flow). Optional.
  If not set, 401 errors prompt the user to manually refresh cookies.
- CANVAS_UPLOAD_SIZE_LIMIT_MB — max file upload size in MB. Default: 100.
"""

import os
from pathlib import Path

# Required: Canvas instance URL, no trailing slash
_raw_url = os.environ.get("CANVAS_BASE_URL", "").rstrip("/")
CANVAS_URL = _raw_url
API_BASE = f"{_raw_url}/api/v1" if _raw_url else ""

# Cookie storage (JSON array of {"name", "value"} objects)
COOKIE_FILE = Path(
    os.environ.get("CANVAS_COOKIE_FILE")
    or Path.home() / ".canvas-cli" / "cookies.json"
)

# Optional cookie refresh script; if unset, 401 errors abort with a manual-refresh prompt.
_token_script = os.environ.get("CANVAS_TOKEN_REFRESH_SCRIPT")
GET_TOKEN_SCRIPT = Path(_token_script) if _token_script else None

# User-level data directories
COURSE_CACHE_FILE = Path.home() / ".canvas-cli" / "courses.json"
RECEIPTS_DIR = Path.home() / ".canvas-cli" / "submissions"

# Upload + network tuning
UPLOAD_SIZE_LIMIT_BYTES = int(os.environ.get("CANVAS_UPLOAD_SIZE_LIMIT_MB", "100")) * 1024 * 1024
PER_PAGE = 100
MAX_RETRIES = 3
INITIAL_BACKOFF = 2


def require_base_url() -> None:
    """Raise a clear error if CANVAS_BASE_URL isn't configured.

    Called by commands that actually need to talk to Canvas.
    """
    if not CANVAS_URL:
        raise RuntimeError(
            "CANVAS_BASE_URL is not set. "
            "Set it to your Canvas instance URL, e.g.:\n"
            "  export CANVAS_BASE_URL=https://canvas.instructure.com\n"
            "or on Windows PowerShell:\n"
            "  $env:CANVAS_BASE_URL = 'https://canvas.instructure.com'"
        )
