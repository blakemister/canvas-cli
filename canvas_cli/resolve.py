"""Course identifier resolution with local cache.

Canvas assigns each course a numeric ID plus a short course_code
(e.g. "CS101-01"). Users typically remember the code; Canvas API
endpoints need the ID. resolve_course() accepts either and returns
the numeric ID.

Resolution order:
1. If the input is numeric, return as-is.
2. Check ~/.canvas-cli/courses.json (cache of previous API lookups).
3. Fetch the user's active courses from Canvas, rebuild the cache, retry.
4. Fall back to returning the input as-is (Canvas will error if wrong).
"""

import json

from .config import COURSE_CACHE_FILE
from .client import get_paginated


def resolve_course(identifier: str) -> str:
    """Resolve course code/name to Canvas numeric ID."""
    if identifier.isdigit():
        return identifier

    upper = _strip_section_suffix(identifier.upper())

    cache = _load_cache()
    if identifier in cache:
        return cache[identifier]
    for k, v in cache.items():
        if k.upper() == upper:
            return v

    # Fetch from API and rebuild cache
    courses = get_paginated("/courses", {"enrollment_state": "active"})
    cache = {}
    for c in courses:
        code = c.get("course_code", "")
        if not code:
            continue
        cache[code] = str(c["id"])
        base = _strip_section_suffix(code)
        if base != code:
            cache[base] = str(c["id"])
    _save_cache(cache)

    if identifier in cache:
        return cache[identifier]
    for k, v in cache.items():
        if k.upper() == upper:
            return v

    return identifier


def list_active_course_ids() -> list[str]:
    """Return Canvas IDs for all of the user's currently-active courses.

    Used by briefing / sync-all to iterate known courses. Refreshes the
    local cache as a side-effect.
    """
    courses = get_paginated("/courses", {"enrollment_state": "active"})
    cache = _load_cache()
    ids = []
    for c in courses:
        cid = str(c["id"])
        ids.append(cid)
        code = c.get("course_code", "")
        if code:
            cache[code] = cid
            base = _strip_section_suffix(code)
            if base != code:
                cache[base] = cid
    _save_cache(cache)
    return ids


def _strip_section_suffix(code: str) -> str:
    """Drop a trailing '-N' section suffix if present (e.g. 'CS101-01' -> 'CS101')."""
    parts = code.split("-")
    if len(parts) > 1 and parts[-1].isdigit():
        return "-".join(parts[:-1])
    return code


def _load_cache() -> dict:
    if COURSE_CACHE_FILE.exists():
        try:
            return json.loads(COURSE_CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict):
    COURSE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COURSE_CACHE_FILE.write_text(json.dumps(cache))
