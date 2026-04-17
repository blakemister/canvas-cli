"""Receipts module tests."""

import hashlib
import json

import pytest

from canvas_cli.receipts import (
    find_prior_receipts,
    hash_file,
    hash_text,
    write_receipt,
)


def test_hash_text_matches_sha256():
    text = "Hello, world"
    assert hash_text(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_hash_file_matches_sha256(tmp_path):
    f = tmp_path / "sample.txt"
    payload = b"some bytes\nof content\n"
    f.write_bytes(payload)
    assert hash_file(f) == hashlib.sha256(payload).hexdigest()


def test_hash_file_large_streams(tmp_path):
    """hash_file should handle files larger than its chunk size."""
    f = tmp_path / "big.bin"
    content = b"abcd" * 100_000  # 400KB
    f.write_bytes(content)
    assert hash_file(f) == hashlib.sha256(content).hexdigest()


def test_write_receipt_creates_parent_dirs(tmp_receipts_dir):
    receipt = {"foo": "bar"}
    path = write_receipt("TEST101", "12345", receipt)
    assert path.exists()
    assert path.parent == tmp_receipts_dir
    assert json.loads(path.read_text()) == receipt


def test_write_receipt_dry_run_goes_to_subdir(tmp_receipts_dir):
    path = write_receipt("TEST101", "12345", {"x": 1}, dry_run=True)
    assert path.parent == tmp_receipts_dir / "dry-run"


def test_find_prior_receipts_returns_real_only(tmp_receipts_dir):
    write_receipt("TEST101", "12345", {"n": 1})
    write_receipt("TEST101", "12345", {"n": 2})
    write_receipt("TEST101", "12345", {"n": 3}, dry_run=True)
    write_receipt("OTHER", "99999", {"n": 99})

    priors = find_prior_receipts("TEST101", "12345")
    assert len(priors) == 2
    # Ensure dry-run subdir is excluded
    for p in priors:
        assert "dry-run" not in str(p)


def test_find_prior_receipts_empty_when_no_dir(tmp_receipts_dir):
    # Don't write anything
    assert find_prior_receipts("NOPE", "0") == []
