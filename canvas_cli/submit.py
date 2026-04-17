"""Canvas assignment submission command with multi-layered safety gates."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .client import CanvasError
from .output import success
from .receipts import (
    find_prior_receipts,
    hash_file,
    hash_text,
    write_receipt,
)
from .resolve import resolve_course
from .submissions_api import (
    fetch_assignment,
    submit_file,
    submit_text,
    submit_url,
)

TYPE_MAP = {
    "text": "online_text_entry",
    "file": "online_upload",
    "url": "online_url",
}

CONFIRM_ATTEMPTS = 3


class GateError(click.UsageError):
    """Raised by a safety gate when submission should be aborted.

    Subclass of click.UsageError so the main CLI's ErrorHandlingGroup and
    Click itself exit with code 2 and print a clean message.
    """


def _validate_flags(
    submission_type: str,
    text_file: Path | None,
    file_path: Path | None,
    submit_url_value: str | None,
) -> tuple[str, Path | str]:
    """Ensure exactly one content flag is provided, matching the declared type.

    Returns (canvas_submission_type, content) where content is a Path for
    text/file and a str for url.
    """
    canvas_type = TYPE_MAP[submission_type]
    flag_map = {
        "text": ("--text-file", text_file),
        "file": ("--file", file_path),
        "url": ("--url", submit_url_value),
    }
    expected_flag, expected_value = flag_map[submission_type]

    other_flags = [
        (name, val) for key, (name, val) in flag_map.items()
        if key != submission_type and val is not None
    ]
    if other_flags:
        names = ", ".join(name for name, _ in other_flags)
        raise GateError(
            f"--type {submission_type} expects {expected_flag}, not {names}."
        )
    if expected_value is None:
        raise GateError(
            f"--type {submission_type} requires {expected_flag}."
        )
    return canvas_type, expected_value


def _gate_assignment_type(assignment: dict, canvas_type: str) -> None:
    """Block if assignment does not allow this submission type."""
    allowed = assignment.get("submission_types") or []
    if canvas_type not in allowed:
        raise GateError(
            f"Assignment does not allow {canvas_type} submissions. "
            f"Allowed: {', '.join(allowed) or '(none)'}"
        )


def _gate_locked(assignment: dict) -> None:
    """Block if assignment is locked for the user."""
    if assignment.get("locked_for_user"):
        lock_at = assignment.get("lock_at") or "unknown time"
        raise GateError(f"Assignment is locked for submission (lock_at={lock_at}).")


def _gate_extension(file_path: Path, assignment: dict) -> None:
    """Block if file extension isn't in assignment.allowed_extensions."""
    allowed = assignment.get("allowed_extensions") or []
    if not allowed:
        return
    ext = file_path.suffix.lstrip(".").lower()
    # Canvas stores extensions without leading dots, but normalize just in case.
    allowed_lower = [a.lstrip(".").lower() for a in allowed]
    if ext not in allowed_lower:
        raise GateError(
            f"File extension '.{ext}' not in allowed extensions "
            f"({', '.join('.' + a for a in allowed_lower)})."
        )


def _gate_nonempty_file(file_path: Path) -> None:
    """Block 0-byte files."""
    if file_path.stat().st_size == 0:
        raise GateError(f"File is empty: {file_path}")


def _gate_nonempty_text(text: str) -> None:
    """Block empty text submissions."""
    if not text.strip():
        raise GateError("Text submission is empty (whitespace-only).")


def _gate_size_limit(file_path: Path, limit_bytes: int) -> None:
    """Block files over the upload size limit."""
    size = file_path.stat().st_size
    if size > limit_bytes:
        raise GateError(
            f"File size {size:,} bytes exceeds limit {limit_bytes:,} bytes."
        )


def _gate_resubmit(
    course_code: str,
    assignment_id: str,
    assignment: dict,
    resubmit_flag: bool,
) -> tuple[bool, list[Path]]:
    """Check for prior submission.

    Blocks without --resubmit if EITHER Canvas shows a prior submission OR
    a local receipt exists. Returns (prior_exists, prior_receipt_paths).
    """
    canvas_submitted_at = (
        (assignment.get("submission") or {}).get("submitted_at")
    )
    receipts = find_prior_receipts(course_code, assignment_id)
    prior_exists = bool(canvas_submitted_at) or bool(receipts)

    if prior_exists and not resubmit_flag:
        msg = "A prior submission exists for this assignment:\n"
        if canvas_submitted_at:
            msg += f"  Canvas submitted_at: {canvas_submitted_at}\n"
        if receipts:
            msg += f"  Local receipts: {len(receipts)} prior\n"
        msg += "Pass --resubmit to create a new attempt."
        raise GateError(msg)

    return prior_exists, receipts


def _gate_late(assignment: dict) -> bool:
    """Return True if submission is past due. Never blocks — warns only."""
    due_at_str = assignment.get("due_at")
    if not due_at_str:
        return False
    try:
        due_at = datetime.fromisoformat(due_at_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) > due_at


def _render_due(assignment: dict) -> str:
    due_at = assignment.get("due_at") or "(no due date)"
    return due_at


def _preview(
    course_code: str,
    assignment: dict,
    submission_type: str,
    canvas_type: str,
    content: Path | str,
    content_hash: str,
    is_late: bool,
    prior_receipts: list[Path],
    resubmit: bool,
) -> str:
    """Print preview to stderr; return the confirm token (last 4 of assignment id)."""
    aid = str(assignment["id"])
    token = aid[-4:] if len(aid) >= 4 else aid

    lines = [
        "=" * 60,
        "SUBMISSION PREVIEW",
        "=" * 60,
        f"Course:       {course_code}",
        f"Assignment:   {assignment.get('name', '?')}",
        f"URL:          {assignment.get('html_url', '?')}",
        f"Points:       {assignment.get('points_possible', '?')}",
        f"Due:          {_render_due(assignment)}",
        f"Type:         {canvas_type}",
    ]

    allowed_ext = assignment.get("allowed_extensions") or []
    if allowed_ext:
        lines.append(f"Extensions:   {', '.join('.' + e for e in allowed_ext)}")

    if submission_type == "url":
        lines.append(f"URL:          {content}")
    elif submission_type == "text":
        path = content if isinstance(content, Path) else None
        if path is not None:
            size = path.stat().st_size
            lines.append(f"Source file:  {path}")
            lines.append(f"Size:         {size:,} bytes")
    else:  # file
        path = content  # type: ignore[assignment]
        size = path.stat().st_size
        lines.append(f"File:         {path}")
        lines.append(f"Size:         {size:,} bytes")

    lines.append(f"SHA-256:      {content_hash[:12]}...{content_hash[-8:]}")

    if is_late:
        lines.append("WARNING:      Submission is LATE (past due_at).")

    if prior_receipts:
        lines.append(f"Prior:        {len(prior_receipts)} local receipt(s)")
        if resubmit:
            lines.append("Mode:         RESUBMIT (--resubmit flag set)")

    canvas_sub = (assignment.get("submission") or {})
    if canvas_sub.get("submitted_at"):
        lines.append(f"Canvas prior: {canvas_sub.get('submitted_at')} (attempt {canvas_sub.get('attempt')})")

    lines.append("=" * 60)
    lines.append(f"CONFIRM TOKEN: type the last 4 of assignment ID ('{token}') to confirm.")
    if resubmit:
        lines.append("RESUBMIT:      also type the word RESUBMIT on next line.")
    lines.append("=" * 60)

    click.echo("\n".join(lines), err=True)
    return token


def _confirm(
    token: str,
    confirm_flag_value: str | None,
    resubmit: bool,
) -> None:
    """Require typed confirmation. Raises click.Abort on failure."""
    # Non-TTY path — must have --confirm flag
    if confirm_flag_value is not None:
        if confirm_flag_value != token:
            raise GateError(
                f"--confirm value '{confirm_flag_value}' does not match token '{token}'."
            )
        if resubmit:
            # For non-interactive resubmit, require RESUBMIT env var (belt + suspenders)
            if os.environ.get("CANVAS_I_UNDERSTAND_RESUBMIT") != "1":
                raise GateError(
                    "Non-interactive resubmit requires CANVAS_I_UNDERSTAND_RESUBMIT=1 env var."
                )
        return

    # Interactive path
    if not sys.stdin.isatty():
        raise GateError(
            "stdin is not a TTY and --confirm was not provided. "
            "For scripted use: pass --confirm <token>. "
            "For resubmit: also set CANVAS_I_UNDERSTAND_RESUBMIT=1."
        )

    for attempt in range(CONFIRM_ATTEMPTS):
        response = click.prompt(
            f"Type confirmation token (attempt {attempt + 1}/{CONFIRM_ATTEMPTS})",
            default="",
            show_default=False,
            prompt_suffix=": ",
        ).strip()
        if response == token:
            break
        click.echo(f"Token mismatch. Expected last 4 of assignment ID.", err=True)
    else:
        raise click.Abort()

    if resubmit:
        response = click.prompt(
            "Type RESUBMIT (case-sensitive) to confirm resubmission",
            default="",
            show_default=False,
            prompt_suffix=": ",
        ).strip()
        if response != "RESUBMIT":
            click.echo("Resubmit token mismatch.", err=True)
            raise click.Abort()


def _recheck_file_hash(file_path: Path, original_hash: str) -> str:
    """Re-hash file. Raises GateError on mismatch. Returns new hash."""
    new_hash = hash_file(file_path)
    if new_hash != original_hash:
        raise GateError(
            "File was modified between preview and confirmation. "
            "Re-run `canvas submit` to generate a fresh preview."
        )
    return new_hash


def _build_receipt(
    course_id: str,
    course_code: str,
    assignment: dict,
    submission_type: str,
    canvas_type: str,
    content: Path | str,
    content_hash: str,
    is_late: bool,
    resubmit: bool,
    prior_receipts: list[Path],
    canvas_response: dict | None,
    dry_run: bool,
) -> dict:
    """Build receipt dict."""
    content_info: dict
    if submission_type == "url":
        content_info = {"kind": "url", "url": content}
    elif submission_type == "text":
        path = content if isinstance(content, Path) else None
        raw = path.read_text(encoding="utf-8", errors="replace") if path else ""
        content_info = {
            "kind": "text",
            "source_file": str(path) if path else None,
            "size_chars": len(raw),
            "sha256": content_hash,
            "preview_first_200_chars": raw[:200],
        }
    else:  # file
        path = content  # type: ignore[assignment]
        canvas_file_id = None
        if canvas_response is not None:
            canvas_file_id = canvas_response.get("_canvas_file_id")
        content_info = {
            "kind": "file",
            "file_path": str(path),
            "file_name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": content_hash,
            "canvas_file_id": canvas_file_id,
        }

    receipt = {
        "schema_version": 1,
        "submitted_at_utc": datetime.now(timezone.utc).isoformat(),
        "course": {"id": course_id, "code": course_code},
        "assignment": {
            "id": str(assignment["id"]),
            "name": assignment.get("name"),
            "html_url": assignment.get("html_url"),
            "due_at": assignment.get("due_at"),
            "points_possible": assignment.get("points_possible"),
        },
        "submission": {
            "type": canvas_type,
            "dry_run": dry_run,
            "resubmit": resubmit,
            "prior_receipt_count": len(prior_receipts),
            "late": is_late,
            "content": content_info,
        },
        "canvas_response": canvas_response,
        "cli_version": "1.0.0",
    }
    return receipt


@click.command()
@click.argument("course")
@click.argument("assignment_id")
@click.option(
    "--type", "submission_type",
    type=click.Choice(["text", "file", "url"], case_sensitive=False),
    required=True,
    help="Submission type. Must be allowed by the assignment.",
)
@click.option(
    "--text-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to text file (markdown/html) for --type text.",
)
@click.option(
    "--file", "file_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to file to upload for --type file.",
)
@click.option(
    "--url", "submit_url_value",
    help="URL to submit for --type url.",
)
@click.option("--dry-run", is_flag=True, help="Run all gates, print preview, exit without calling Canvas.")
@click.option("--resubmit", is_flag=True, help="Allow resubmission when a prior submission exists.")
@click.option(
    "--confirm", "confirm_token",
    help="Pre-supply confirmation token (last 4 of assignment ID). Required when stdin is not a TTY.",
)
@click.pass_context
def submit(
    ctx,
    course,
    assignment_id,
    submission_type,
    text_file,
    file_path,
    submit_url_value,
    dry_run,
    resubmit,
    confirm_token,
):
    """Submit work to a Canvas assignment with safety gates.

    \b
    Examples:
      canvas submit COURSE ASSIGN_ID --type text --text-file ./work/submission.md
      canvas submit COURSE ASSIGN_ID --type file --file ./lab.ipynb
      canvas submit COURSE ASSIGN_ID --type url --url https://example.com/my-work
      canvas submit COURSE ASSIGN_ID --type file --file ./lab.ipynb --dry-run
      canvas submit COURSE ASSIGN_ID --type file --file ./lab.ipynb --resubmit
    """
    submission_type = submission_type.lower()
    canvas_type, content = _validate_flags(
        submission_type, text_file, file_path, submit_url_value,
    )

    # Resolve course and fetch assignment
    course_id = resolve_course(course)
    try:
        assignment = fetch_assignment(course_id, assignment_id)
    except CanvasError as e:
        raise click.ClickException(
            f"Failed to fetch assignment: {e}. Check COURSE and ASSIGNMENT_ID."
        )

    # Phase 1 gates
    _gate_assignment_type(assignment, canvas_type)
    _gate_locked(assignment)

    # Phase 2 gates: content-specific
    if submission_type == "file":
        _gate_extension(content, assignment)
        _gate_nonempty_file(content)
        from .config import UPLOAD_SIZE_LIMIT_BYTES
        _gate_size_limit(content, UPLOAD_SIZE_LIMIT_BYTES)
        content_hash = hash_file(content)
    elif submission_type == "text":
        text = content.read_text(encoding="utf-8")
        _gate_nonempty_text(text)
        content_hash = hash_text(text)
    else:  # url
        if not str(content).startswith(("http://", "https://")):
            raise GateError(f"URL must start with http:// or https://: {content}")
        content_hash = hash_text(str(content))

    # Resubmit + late checks
    prior_exists, prior_receipts = _gate_resubmit(
        course, assignment_id, assignment, resubmit,
    )
    is_late = _gate_late(assignment)

    # Preview
    token = _preview(
        course, assignment, submission_type, canvas_type,
        content, content_hash, is_late, prior_receipts, resubmit,
    )

    # Dry-run short-circuit
    if dry_run:
        receipt = _build_receipt(
            course_id, course, assignment, submission_type, canvas_type,
            content, content_hash, is_late, resubmit, prior_receipts,
            canvas_response=None, dry_run=True,
        )
        receipt_path = write_receipt(course, assignment_id, receipt, dry_run=True)
        click.echo(f"DRY RUN COMPLETE — no submission made. Receipt: {receipt_path}", err=True)
        if ctx.obj.get("json"):
            success(ctx, {"dry_run": True, "would_submit": receipt, "receipt_path": str(receipt_path)})
        return

    # Confirm
    _confirm(token, confirm_token, resubmit)

    # Re-verify hash for files (stale-file guard)
    if submission_type == "file":
        _recheck_file_hash(content, content_hash)
    elif submission_type == "text":
        current_text = content.read_text(encoding="utf-8")
        if hash_text(current_text) != content_hash:
            raise GateError(
                "Text file was modified between preview and confirmation. "
                "Re-run `canvas submit` for a fresh preview."
            )

    # Execute
    try:
        if submission_type == "text":
            html_body = content.read_text(encoding="utf-8")
            response = submit_text(course_id, assignment_id, html_body)
        elif submission_type == "url":
            response = submit_url(course_id, assignment_id, str(content))
        else:  # file
            response = submit_file(course_id, assignment_id, content)
    except CanvasError as e:
        detail_msg = f" Detail: {e.detail}" if e.detail else ""
        raise click.ClickException(f"Canvas rejected the submission: {e}.{detail_msg}")

    # Write receipt
    receipt = _build_receipt(
        course_id, course, assignment, submission_type, canvas_type,
        content, content_hash, is_late, resubmit, prior_receipts,
        canvas_response=response, dry_run=False,
    )
    receipt_path = write_receipt(course, assignment_id, receipt)

    click.echo(
        f"SUBMITTED: attempt {response.get('attempt', '?')} at "
        f"{response.get('submitted_at', '?')}. Receipt: {receipt_path}",
        err=True,
    )
    if ctx.obj.get("json"):
        success(ctx, {
            "submission_id": response.get("id"),
            "attempt": response.get("attempt"),
            "submitted_at": response.get("submitted_at"),
            "workflow_state": response.get("workflow_state"),
            "receipt_path": str(receipt_path),
        })
