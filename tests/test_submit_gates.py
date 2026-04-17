"""Unit tests for each submit safety gate in isolation."""

from pathlib import Path

import pytest

from canvas_cli.submit import (
    GateError,
    _gate_assignment_type,
    _gate_extension,
    _gate_late,
    _gate_locked,
    _gate_nonempty_file,
    _gate_nonempty_text,
    _gate_resubmit,
    _gate_size_limit,
    _validate_flags,
)


# --- _validate_flags ---

def test_validate_flags_text_happy(sample_text_file):
    t, content = _validate_flags("text", sample_text_file, None, None)
    assert t == "online_text_entry"
    assert content == sample_text_file


def test_validate_flags_file_happy(sample_notebook):
    t, content = _validate_flags("file", None, sample_notebook, None)
    assert t == "online_upload"
    assert content == sample_notebook


def test_validate_flags_url_happy():
    t, content = _validate_flags("url", None, None, "https://example.com")
    assert t == "online_url"
    assert content == "https://example.com"


def test_validate_flags_rejects_wrong_flag_for_type(sample_notebook):
    with pytest.raises(GateError, match="--type text expects --text-file"):
        _validate_flags("text", None, sample_notebook, None)


def test_validate_flags_rejects_missing_flag():
    with pytest.raises(GateError, match="requires --text-file"):
        _validate_flags("text", None, None, None)


def test_validate_flags_rejects_multiple_flags(sample_text_file, sample_notebook):
    with pytest.raises(GateError, match="not --file"):
        _validate_flags("text", sample_text_file, sample_notebook, None)


# --- _gate_assignment_type ---

def test_gate_assignment_type_pass(assignment_text):
    _gate_assignment_type(assignment_text, "online_text_entry")  # no raise


def test_gate_assignment_type_fail(assignment_text):
    with pytest.raises(GateError, match="does not allow online_upload"):
        _gate_assignment_type(assignment_text, "online_upload")


# --- _gate_locked ---

def test_gate_locked_pass(assignment_text):
    _gate_locked(assignment_text)


def test_gate_locked_fail(assignment_locked):
    with pytest.raises(GateError, match="locked"):
        _gate_locked(assignment_locked)


# --- _gate_extension ---

def test_gate_extension_allowed(sample_notebook, assignment_upload):
    _gate_extension(sample_notebook, assignment_upload)


def test_gate_extension_rejected(sample_text_file, assignment_upload):
    with pytest.raises(GateError, match="not in allowed extensions"):
        _gate_extension(sample_text_file, assignment_upload)


def test_gate_extension_empty_allowed_skips(sample_text_file, assignment_text):
    # allowed_extensions = [] → anything goes
    _gate_extension(sample_text_file, assignment_text)


# --- _gate_nonempty ---

def test_gate_nonempty_file_pass(sample_notebook):
    _gate_nonempty_file(sample_notebook)


def test_gate_nonempty_file_fail(empty_file):
    with pytest.raises(GateError, match="empty"):
        _gate_nonempty_file(empty_file)


def test_gate_nonempty_text_pass():
    _gate_nonempty_text("hello")


def test_gate_nonempty_text_fail_empty():
    with pytest.raises(GateError, match="empty"):
        _gate_nonempty_text("")


def test_gate_nonempty_text_fail_whitespace():
    with pytest.raises(GateError, match="empty"):
        _gate_nonempty_text("   \n\t  ")


# --- _gate_size_limit ---

def test_gate_size_limit_pass(sample_notebook):
    _gate_size_limit(sample_notebook, 1_000_000)  # generous


def test_gate_size_limit_fail(sample_notebook):
    with pytest.raises(GateError, match="exceeds limit"):
        _gate_size_limit(sample_notebook, 1)


# --- _gate_resubmit ---

def test_gate_resubmit_no_prior(assignment_text, tmp_receipts_dir):
    prior_exists, receipts = _gate_resubmit("TEST101", "12345", assignment_text, False)
    assert prior_exists is False
    assert receipts == []


def test_gate_resubmit_canvas_prior_blocks(assignment_submitted, tmp_receipts_dir):
    with pytest.raises(GateError, match="prior submission"):
        _gate_resubmit("TEST101", "44444", assignment_submitted, False)


def test_gate_resubmit_canvas_prior_allowed_with_flag(assignment_submitted, tmp_receipts_dir):
    prior_exists, _ = _gate_resubmit("TEST101", "44444", assignment_submitted, True)
    assert prior_exists is True


def test_gate_resubmit_local_receipt_blocks(assignment_text, tmp_receipts_dir):
    from canvas_cli.receipts import write_receipt
    write_receipt("TEST101", "12345", {"n": 1})
    with pytest.raises(GateError, match="prior submission"):
        _gate_resubmit("TEST101", "12345", assignment_text, False)


# --- _gate_late ---

def test_gate_late_not_late(assignment_text):
    assert _gate_late(assignment_text) is False


def test_gate_late_past_due(assignment_late):
    assert _gate_late(assignment_late) is True


def test_gate_late_no_due_date():
    assert _gate_late({"due_at": None}) is False
