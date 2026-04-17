"""Submission receipt writer and hashing utilities."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import RECEIPTS_DIR


def hash_file(path: Path) -> str:
    """Return sha256 hex digest of file contents. Streams in 64KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def hash_text(text: str) -> str:
    """Return sha256 hex digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp_for_filename() -> str:
    """UTC timestamp + nanosecond suffix for uniqueness on Windows where
    datetime has millisecond granularity and back-to-back calls collide."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return f"{stamp}-{time.perf_counter_ns() % 10_000_000:07d}"


def _receipts_dir(dry_run: bool = False) -> Path:
    """Return the receipts directory, with dry-run in a subdir."""
    base = RECEIPTS_DIR
    return base / "dry-run" if dry_run else base


def write_receipt(
    course_code: str,
    assignment_id: str,
    receipt: dict,
    dry_run: bool = False,
) -> Path:
    """Write receipt JSON to the receipts dir. Creates parent dirs as needed.

    Filename: {course_code}_{assignment_id}_{UTC-timestamp}.json
    Dry-run receipts go in a 'dry-run' subdirectory and do not count toward
    resubmission detection.
    """
    dir_path = _receipts_dir(dry_run)
    dir_path.mkdir(parents=True, exist_ok=True)
    safe_course = course_code.replace("/", "_").replace("\\", "_")
    filename = f"{safe_course}_{assignment_id}_{_timestamp_for_filename()}.json"
    path = dir_path / filename
    path.write_text(json.dumps(receipt, indent=2, default=str))
    return path


def find_prior_receipts(course_code: str, assignment_id: str) -> list[Path]:
    """Return sorted list of prior (non-dry-run) receipts for this assignment."""
    dir_path = _receipts_dir(dry_run=False)
    if not dir_path.exists():
        return []
    safe_course = course_code.replace("/", "_").replace("\\", "_")
    pattern = f"{safe_course}_{assignment_id}_*.json"
    return sorted(dir_path.glob(pattern))
