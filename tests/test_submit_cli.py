"""End-to-end CLI tests for canvas submit using CliRunner + httpx mock."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from canvas_cli.main import cli


@pytest.fixture
def patched_api(assignment_text, monkeypatch):
    """Patch submissions_api functions at the module level.

    Avoids network entirely. Returns a dict of call-tracking lists so tests
    can assert on what the CLI tried to send to Canvas.
    """
    calls = {"fetch": [], "submit_text": [], "submit_url": [], "submit_file": []}

    def _fetch(cid, aid):
        calls["fetch"].append((cid, aid))
        # Default: return assignment_text fixture (overridden per test)
        return assignment_text

    def _submit_text(cid, aid, body):
        calls["submit_text"].append((cid, aid, body))
        return {
            "id": 9001, "attempt": 1, "workflow_state": "submitted",
            "submitted_at": "2026-04-17T12:00:00Z", "late": False,
        }

    def _submit_url(cid, aid, url):
        calls["submit_url"].append((cid, aid, url))
        return {
            "id": 9002, "attempt": 1, "workflow_state": "submitted",
            "submitted_at": "2026-04-17T12:00:00Z",
        }

    def _submit_file(cid, aid, path):
        calls["submit_file"].append((cid, aid, path))
        return {
            "id": 9003, "attempt": 1, "workflow_state": "submitted",
            "submitted_at": "2026-04-17T12:00:00Z",
            "_canvas_file_id": 77777,
        }

    monkeypatch.setattr("canvas_cli.submit.fetch_assignment", _fetch)
    monkeypatch.setattr("canvas_cli.submit.submit_text", _submit_text)
    monkeypatch.setattr("canvas_cli.submit.submit_url", _submit_url)
    monkeypatch.setattr("canvas_cli.submit.submit_file", _submit_file)
    return calls


@pytest.fixture
def resolver(monkeypatch):
    """Bypass Canvas course resolution."""
    monkeypatch.setattr("canvas_cli.submit.resolve_course", lambda x: "99999")


# --- Dry-run tests (critical: no Canvas call made) ---

def test_dry_run_text_no_canvas_call(
    runner, patched_api, resolver, sample_text_file, tmp_receipts_dir,
):
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text", "--text-file", str(sample_text_file),
        "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert "DRY RUN COMPLETE" in result.output
    # Assignment was fetched (always) but no submit POST
    assert len(patched_api["fetch"]) == 1
    assert patched_api["submit_text"] == []
    assert patched_api["submit_url"] == []
    assert patched_api["submit_file"] == []
    # Dry-run receipt written in dry-run subdir
    dry_receipts = list((tmp_receipts_dir / "dry-run").glob("*.json"))
    assert len(dry_receipts) == 1


def test_dry_run_url_no_canvas_call(
    runner, patched_api, resolver, assignment_url, monkeypatch, tmp_receipts_dir,
):
    # Override fetch to return URL-type assignment
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_url)
    result = runner.invoke(cli, [
        "submit", "TEST101", "33333",
        "--type", "url", "--url", "https://example.com/my-work",
        "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert "DRY RUN COMPLETE" in result.output
    assert patched_api["submit_url"] == []


def test_dry_run_file_no_canvas_call(
    runner, patched_api, resolver, assignment_upload, monkeypatch,
    sample_notebook, tmp_receipts_dir,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_upload)
    result = runner.invoke(cli, [
        "submit", "TEST101", "22222",
        "--type", "file", "--file", str(sample_notebook),
        "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert patched_api["submit_file"] == []


def test_dry_run_json_mode(
    runner, patched_api, resolver, sample_text_file, tmp_receipts_dir,
):
    result = runner.invoke(cli, [
        "--json", "submit", "TEST101", "12345",
        "--type", "text", "--text-file", str(sample_text_file),
        "--dry-run",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["data"]["dry_run"] is True
    assert "would_submit" in payload["data"]


# --- Happy-path submits with --confirm ---

def test_submit_text_happy_path(
    runner, patched_api, resolver, sample_text_file, tmp_receipts_dir,
):
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "2345",  # last 4 of 12345
    ])
    assert result.exit_code == 0, result.output
    assert len(patched_api["submit_text"]) == 1
    # Receipt written to main dir
    receipts = list(tmp_receipts_dir.glob("TEST101_12345_*.json"))
    assert len(receipts) == 1
    data = json.loads(receipts[0].read_text())
    assert data["canvas_response"]["id"] == 9001
    assert data["submission"]["type"] == "online_text_entry"


def test_submit_url_happy_path(
    runner, patched_api, resolver, assignment_url, monkeypatch, tmp_receipts_dir,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_url)
    result = runner.invoke(cli, [
        "submit", "TEST101", "33333",
        "--type", "url", "--url", "https://example.com/my-work",
        "--confirm", "3333",
    ])
    assert result.exit_code == 0, result.output
    assert patched_api["submit_url"][0][2] == "https://example.com/my-work"


def test_submit_file_happy_path(
    runner, patched_api, resolver, assignment_upload, monkeypatch,
    sample_notebook, tmp_receipts_dir,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_upload)
    result = runner.invoke(cli, [
        "submit", "TEST101", "22222",
        "--type", "file", "--file", str(sample_notebook),
        "--confirm", "2222",
    ])
    assert result.exit_code == 0, result.output
    assert len(patched_api["submit_file"]) == 1
    receipts = list(tmp_receipts_dir.glob("TEST101_22222_*.json"))
    data = json.loads(receipts[0].read_text())
    assert data["submission"]["content"]["canvas_file_id"] == 77777


# --- Gate rejections ---

def test_wrong_confirm_token_rejected(
    runner, patched_api, resolver, sample_text_file, tmp_receipts_dir,
):
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "9999",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_text"] == []


def test_type_mismatch_rejected(
    runner, patched_api, resolver, monkeypatch, assignment_upload, sample_text_file,
):
    # Assignment accepts only online_upload but we passed --type text
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_upload)
    result = runner.invoke(cli, [
        "submit", "TEST101", "22222",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "2222",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_text"] == []


def test_extension_rejected(
    runner, patched_api, resolver, monkeypatch, assignment_upload, sample_text_file,
):
    # .md is not in allowed_extensions (only ipynb, py)
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_upload)
    result = runner.invoke(cli, [
        "submit", "TEST101", "22222",
        "--type", "file", "--file", str(sample_text_file),
        "--confirm", "2222",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_file"] == []


def test_empty_file_rejected(
    runner, patched_api, resolver, monkeypatch, assignment_upload, tmp_path,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_upload)
    empty = tmp_path / "empty.ipynb"
    empty.touch()
    result = runner.invoke(cli, [
        "submit", "TEST101", "22222",
        "--type", "file", "--file", str(empty),
        "--confirm", "2222",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_file"] == []


def test_already_submitted_without_resubmit_flag(
    runner, patched_api, resolver, monkeypatch, assignment_submitted, sample_text_file,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_submitted)
    result = runner.invoke(cli, [
        "submit", "TEST101", "44444",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "4444",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_text"] == []


def test_already_submitted_with_resubmit_and_env(
    runner, patched_api, resolver, monkeypatch, assignment_submitted,
    sample_text_file, tmp_receipts_dir,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_submitted)
    monkeypatch.setenv("CANVAS_I_UNDERSTAND_RESUBMIT", "1")
    result = runner.invoke(cli, [
        "submit", "TEST101", "44444",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "4444",
        "--resubmit",
    ])
    assert result.exit_code == 0, result.output
    assert len(patched_api["submit_text"]) == 1


def test_resubmit_without_env_var_blocked(
    runner, patched_api, resolver, monkeypatch, assignment_submitted, sample_text_file,
):
    # --resubmit flag present, --confirm present, but no env var → abort
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_submitted)
    monkeypatch.delenv("CANVAS_I_UNDERSTAND_RESUBMIT", raising=False)
    result = runner.invoke(cli, [
        "submit", "TEST101", "44444",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "4444",
        "--resubmit",
    ])
    assert result.exit_code != 0
    assert "CANVAS_I_UNDERSTAND_RESUBMIT" in result.output
    assert patched_api["submit_text"] == []


def test_locked_assignment_rejected(
    runner, patched_api, resolver, monkeypatch, assignment_locked, sample_text_file,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_locked)
    result = runner.invoke(cli, [
        "submit", "TEST101", "66666",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "6666",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_text"] == []


def test_multiple_content_flags_rejected(
    runner, resolver, sample_text_file, sample_notebook,
):
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text",
        "--text-file", str(sample_text_file),
        "--file", str(sample_notebook),
        "--confirm", "2345",
    ])
    assert result.exit_code != 0


def test_missing_content_flag_rejected(runner, resolver):
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text",
        "--confirm", "2345",
    ])
    assert result.exit_code != 0


# --- Late warning ---

def test_late_submission_warns_but_submits(
    runner, patched_api, resolver, monkeypatch, assignment_late, sample_text_file,
):
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_late)
    result = runner.invoke(cli, [
        "submit", "TEST101", "55555",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "5555",
    ])
    assert result.exit_code == 0, result.output
    # "LATE" should appear somewhere in the preview
    assert "LATE" in result.output or "late" in result.output.lower()
    assert len(patched_api["submit_text"]) == 1


# --- Regression guards ---

def test_dry_run_never_posts_to_submissions(
    runner, patched_api, resolver, sample_text_file, tmp_receipts_dir,
):
    """REGRESSION GUARD: dry-run MUST NOT call any submit_* function."""
    for _ in range(3):
        result = runner.invoke(cli, [
            "submit", "TEST101", "12345",
            "--type", "text", "--text-file", str(sample_text_file),
            "--dry-run",
        ])
        assert result.exit_code == 0
    assert patched_api["submit_text"] == []
    assert patched_api["submit_url"] == []
    assert patched_api["submit_file"] == []


def test_receipt_written_on_success(
    runner, patched_api, resolver, sample_text_file, tmp_receipts_dir,
):
    """REGRESSION GUARD: every successful submit writes a receipt."""
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "2345",
    ])
    assert result.exit_code == 0
    receipts = list(tmp_receipts_dir.glob("*.json"))
    assert len(receipts) == 1


def test_confirm_flag_must_match_last_4_of_aid(
    runner, patched_api, resolver, sample_text_file,
):
    """REGRESSION GUARD: --confirm must equal exactly the last 4 digits."""
    # close-but-wrong: reversed digits
    result = runner.invoke(cli, [
        "submit", "TEST101", "12345",
        "--type", "text", "--text-file", str(sample_text_file),
        "--confirm", "5432",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_text"] == []


def test_modified_file_between_preview_and_submit(
    runner, patched_api, resolver, monkeypatch, assignment_upload, tmp_path,
    tmp_receipts_dir,
):
    """REGRESSION GUARD: hash re-verification catches mid-flow file edits."""
    monkeypatch.setattr("canvas_cli.submit.fetch_assignment",
                        lambda c, a: assignment_upload)

    f = tmp_path / "test.ipynb"
    f.write_text('{"cells": [], "nbformat": 4}')

    # Patch hash_file so the second call (during recheck) returns a different hash
    call_count = {"n": 0}
    original_hash_file = None

    from canvas_cli import receipts as receipts_module
    from canvas_cli import submit as submit_module
    real_hash = receipts_module.hash_file

    def fake_hash(path):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_hash(path)
        return "DIFFERENT_HASH"

    monkeypatch.setattr("canvas_cli.submit.hash_file", fake_hash)

    result = runner.invoke(cli, [
        "submit", "TEST101", "22222",
        "--type", "file", "--file", str(f),
        "--confirm", "2222",
    ])
    assert result.exit_code != 0
    assert patched_api["submit_file"] == []
