"""Async and sync HTTP clients for Canvas LMS API with connection reuse, pagination, and retry."""

import asyncio
import time
from pathlib import Path
from urllib.parse import unquote, urlencode

import httpx

from .auth import load_cookies, refresh_cookies
from .config import API_BASE, GET_TOKEN_SCRIPT, INITIAL_BACKOFF, MAX_RETRIES, PER_PAGE

# Concurrency limiter for async requests (Canvas rate limit safety)
_semaphore = asyncio.Semaphore(10)


class CanvasError(Exception):
    """Base Canvas API error."""
    def __init__(self, message, status_code=None, detail=None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class AuthError(CanvasError):
    """Authentication failed after refresh attempt."""


class NetworkError(CanvasError):
    """Connection or timeout error."""


def _extract_csrf_token(cookie_header: str) -> str | None:
    """Pull the _csrf_token value out of the Cookie header string and URL-decode it."""
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "_csrf_token" and value:
            return unquote(value)
    return None


# ---------------------------------------------------------------------------
# Sync client (for simple single-call commands: auth, page, thread, rubric, download, submit)
# ---------------------------------------------------------------------------

_sync_client: httpx.Client | None = None


def _get_sync_client() -> httpx.Client:
    """Return a module-level sync httpx.Client with connection pooling.

    Used by download_file for streaming downloads. make_request uses
    httpx.request() directly so each request can set its own headers
    (Content-Type for form, X-CSRF-Token per-method) without mutating
    a shared client.
    """
    global _sync_client
    if _sync_client is None:
        _sync_client = httpx.Client(
            headers={"Cookie": load_cookies()},
            timeout=30.0,
            follow_redirects=True,
        )
    return _sync_client


def make_request(
    method: str,
    endpoint: str,
    params: dict | list[tuple] | None = None,
    json_body: dict | None = None,
    form_data: dict | list[tuple] | None = None,
) -> httpx.Response:
    """Make authenticated Canvas API request with retry on 429 and auto-refresh on 401.

    Pass json_body for application/json requests, or form_data for
    application/x-www-form-urlencoded. Mutually exclusive.
    """
    if json_body is not None and form_data is not None:
        raise ValueError("json_body and form_data are mutually exclusive")

    url = f"{API_BASE}{endpoint}"
    is_state_changing = method.lower() in ("post", "put", "patch", "delete")

    def _build_headers() -> dict:
        cookie_header = load_cookies()
        h = {"Cookie": cookie_header}
        # Canvas requires X-CSRF-Token (URL-decoded _csrf_token cookie value)
        # on state-changing requests. GETs never need it.
        if is_state_changing:
            csrf = _extract_csrf_token(cookie_header)
            if csrf:
                h["X-CSRF-Token"] = csrf
        return h

    headers = _build_headers()

    # Pre-encode form_data ourselves with urlencode(doseq=True) — this is
    # rock-solid for list-of-tuples with repeated keys (e.g. submission[file_ids][])
    # which httpx's `data=` handling can mis-serialize in some versions.
    body_content = None
    if form_data is not None:
        body_content = urlencode(form_data, doseq=True).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                content=body_content,
                timeout=30.0,
                follow_redirects=True,
            )

            if resp.status_code == 401:
                if attempt == 0 and refresh_cookies():
                    # Rebuild headers — the refreshed cookie file has a fresh
                    # _csrf_token that must replace the stale one.
                    new_headers = _build_headers()
                    headers["Cookie"] = new_headers["Cookie"]
                    if "X-CSRF-Token" in new_headers:
                        headers["X-CSRF-Token"] = new_headers["X-CSRF-Token"]
                    continue
                raise AuthError(
                    f"Cookie refresh failed. Run: python {GET_TOKEN_SCRIPT}"
                )

            if resp.status_code == 429:
                retry_after = float(
                    resp.headers.get("Retry-After", INITIAL_BACKOFF * (2**attempt))
                )
                time.sleep(retry_after)
                continue

            resp.raise_for_status()
            return resp

        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text[:500] or None
            raise CanvasError(
                f"HTTP {e.response.status_code} for {method.upper()} {endpoint}",
                status_code=e.response.status_code,
                detail=detail,
            )

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt < MAX_RETRIES:
                wait = INITIAL_BACKOFF * (2**attempt)
                time.sleep(wait)
                continue
            raise NetworkError(str(e))

    raise NetworkError(f"Max retries exceeded for {endpoint}")


def get(endpoint: str, params: dict | list[tuple] | None = None) -> dict | list:
    """GET request, return parsed JSON."""
    return make_request("get", endpoint, params=params).json()


def post(
    endpoint: str,
    json_body: dict | None = None,
    params: dict | list[tuple] | None = None,
) -> dict | list:
    """POST with JSON body. Returns parsed JSON response."""
    return make_request("post", endpoint, params=params, json_body=json_body).json()


def post_form(
    endpoint: str,
    form_data: dict | list[tuple],
    params: dict | list[tuple] | None = None,
) -> dict | list:
    """POST with form-encoded body. Returns parsed JSON response.

    Use a list of tuples for repeated keys (e.g. submission[file_ids][]).
    """
    return make_request("post", endpoint, params=params, form_data=form_data).json()


def upload_file_to_url(
    upload_url: str,
    upload_params: dict,
    file_path: Path,
    file_param: str = "file",
) -> dict:
    """Multipart upload to the inst-fs URL returned by Canvas submission-init.

    Does NOT send Canvas session cookies — the upload_params already contain
    signed credentials. Follows redirects (Canvas returns 301 with a
    confirmation URL) to retrieve the final File object.
    """
    with open(file_path, "rb") as fh:
        files = {file_param: (file_path.name, fh)}
        try:
            resp = httpx.post(
                upload_url,
                data=upload_params,
                files=files,
                timeout=120.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text[:500] or None
            raise CanvasError(
                f"File upload failed: HTTP {e.response.status_code}",
                status_code=e.response.status_code,
                detail=detail,
            )
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise NetworkError(f"File upload failed: {e}")

    try:
        return resp.json()
    except ValueError:
        raise CanvasError(
            "File upload returned non-JSON response",
            detail=resp.text[:500],
        )


def get_paginated(endpoint: str, params: dict | list[tuple] | None = None) -> list:
    """GET with page-count pagination. Returns combined results."""
    if params is None:
        base_params = []
    elif isinstance(params, dict):
        base_params = list(params.items())
    else:
        base_params = list(params)

    param_keys = {k for k, _ in base_params}
    if "per_page" not in param_keys:
        base_params.append(("per_page", PER_PAGE))

    effective_per_page = PER_PAGE
    for k, v in base_params:
        if k == "per_page":
            effective_per_page = int(v)

    all_results = []
    page = 1

    while True:
        page_params = base_params + [("page", page)]
        resp = make_request("get", endpoint, params=page_params)
        data = resp.json()

        if not isinstance(data, list) or len(data) == 0:
            break

        all_results.extend(data)

        if len(data) < effective_per_page:
            break

        page += 1

    return all_results


def download_file(url: str, dest: Path) -> Path:
    """Download a file from a URL (following redirects) to dest path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    client = _get_sync_client()

    try:
        with client.stream("GET", url, timeout=60.0) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
    except httpx.HTTPStatusError as e:
        raise CanvasError(
            f"Download failed: HTTP {e.response.status_code} for {url}",
            status_code=e.response.status_code,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        raise NetworkError(f"Download failed: {e}")

    return dest


# ---------------------------------------------------------------------------
# Async client (for parallel multi-call commands: sync, briefing, sync-all)
# ---------------------------------------------------------------------------

def create_client() -> httpx.AsyncClient:
    """Create an AsyncClient with connection pooling and auth cookies."""
    return httpx.AsyncClient(
        headers={"Cookie": load_cookies()},
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=15, max_keepalive_connections=10),
    )


async def async_request(
    client: httpx.AsyncClient,
    method: str,
    endpoint: str,
    params: dict | list[tuple] | None = None,
) -> httpx.Response:
    """Make an authenticated async request with retry, rate-limit, and auth refresh."""
    url = f"{API_BASE}{endpoint}"

    async with _semaphore:
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await client.request(method, url, params=params)

                if resp.status_code == 401:
                    if attempt == 0 and refresh_cookies():
                        client.headers["Cookie"] = load_cookies()
                        continue
                    raise AuthError(
                        f"Cookie refresh failed. Run: python {GET_TOKEN_SCRIPT}"
                    )

                if resp.status_code == 429:
                    retry_after = float(
                        resp.headers.get("Retry-After", INITIAL_BACKOFF * (2**attempt))
                    )
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()
                return resp

            except httpx.HTTPStatusError as e:
                try:
                    detail = e.response.json()
                except Exception:
                    detail = e.response.text[:500] or None
                raise CanvasError(
                    f"HTTP {e.response.status_code} for {method.upper()} {endpoint}",
                    status_code=e.response.status_code,
                    detail=detail,
                )

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < MAX_RETRIES:
                    wait = INITIAL_BACKOFF * (2**attempt)
                    await asyncio.sleep(wait)
                    continue
                raise NetworkError(str(e))

    raise NetworkError(f"Max retries exceeded for {endpoint}")


async def async_get(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict | list[tuple] | None = None,
) -> dict | list:
    """Async GET request, return parsed JSON."""
    resp = await async_request(client, "get", endpoint, params=params)
    return resp.json()


async def async_get_paginated(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict | list[tuple] | None = None,
) -> list:
    """Async GET with page-count pagination. Returns combined results."""
    if params is None:
        base_params = []
    elif isinstance(params, dict):
        base_params = list(params.items())
    else:
        base_params = list(params)

    param_keys = {k for k, _ in base_params}
    if "per_page" not in param_keys:
        base_params.append(("per_page", PER_PAGE))

    effective_per_page = PER_PAGE
    for k, v in base_params:
        if k == "per_page":
            effective_per_page = int(v)

    all_results = []
    page = 1

    while True:
        page_params = base_params + [("page", page)]
        resp = await async_request(client, "get", endpoint, params=page_params)
        data = resp.json()

        if not isinstance(data, list) or len(data) == 0:
            break

        all_results.extend(data)

        if len(data) < effective_per_page:
            break

        page += 1

    return all_results
