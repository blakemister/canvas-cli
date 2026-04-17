"""Cookie-based authentication for Canvas LMS.

Canvas doesn't expose a public OAuth flow for personal use, so this CLI
authenticates by replaying browser session cookies. Extract cookies from
your logged-in browser (via DevTools or an extension) into a JSON file
at the path configured by CANVAS_COOKIE_FILE (default:
~/.canvas-cli/cookies.json).

Cookie file format (JSON array):
    [
      {"name": "canvas_session", "value": "..."},
      {"name": "_csrf_token", "value": "..."},
      ...
    ]

You can automate refresh by setting CANVAS_TOKEN_REFRESH_SCRIPT to a
script that writes a fresh cookie file. Example scripts for common
Canvas SSO flows live in the `examples/` directory of the repo.
"""

import json
import subprocess
import sys

from .config import COOKIE_FILE, GET_TOKEN_SCRIPT

_cookie_cache: str | None = None


def load_cookies() -> str:
    """Load cookies from COOKIE_FILE; return as 'Name=Value; Name=Value' header.

    Caches the joined header string so the file is only read once per CLI invocation.
    """
    global _cookie_cache
    if _cookie_cache is not None:
        return _cookie_cache

    if not COOKIE_FILE.exists():
        print(
            f"Cookie file not found: {COOKIE_FILE}\n"
            "Extract cookies from your logged-in browser and save them as JSON, "
            "or set CANVAS_TOKEN_REFRESH_SCRIPT to automate refresh. "
            "See README for details.",
            file=sys.stderr,
        )
        sys.exit(3)

    try:
        cookies = json.loads(COOKIE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to read cookie file: {e}", file=sys.stderr)
        sys.exit(3)

    if not cookies:
        print(f"Cookie file is empty: {COOKIE_FILE}", file=sys.stderr)
        sys.exit(3)

    _cookie_cache = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return _cookie_cache


def invalidate_cookie_cache() -> None:
    """Clear the cached cookie string so next load_cookies() re-reads the file."""
    global _cookie_cache
    _cookie_cache = None


def refresh_cookies() -> bool:
    """Invoke CANVAS_TOKEN_REFRESH_SCRIPT if configured. Returns True on success.

    If the refresh script isn't configured, returns False so the caller
    surfaces the auth error to the user.
    """
    if GET_TOKEN_SCRIPT is None:
        return False
    if not GET_TOKEN_SCRIPT.exists():
        print(f"Token refresh script not found: {GET_TOKEN_SCRIPT}", file=sys.stderr)
        return False

    result = subprocess.run(
        [sys.executable, str(GET_TOKEN_SCRIPT)],
        cwd=str(GET_TOKEN_SCRIPT.parent),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        invalidate_cookie_cache()
        return True
    return False
