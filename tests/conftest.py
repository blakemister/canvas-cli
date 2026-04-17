"""Shared test fixtures: fake auth, sample fixtures, CliRunner, httpx mocks."""

import json
import os
from pathlib import Path

# Set a fake Canvas URL BEFORE importing canvas_cli (config reads env at import time).
os.environ.setdefault("CANVAS_BASE_URL", "https://canvas.test")

import pytest
from click.testing import CliRunner

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def fake_cookies(monkeypatch):
    """Patch auth to return a fake cookie string without reading any file."""
    monkeypatch.setattr("canvas_cli.auth.load_cookies", lambda: "fake_session=abc123")
    monkeypatch.setattr("canvas_cli.auth.refresh_cookies", lambda: True)


@pytest.fixture(autouse=True)
def tmp_receipts_dir(tmp_path, monkeypatch):
    """Redirect RECEIPTS_DIR to a tmp path for isolated test runs."""
    receipts = tmp_path / "receipts"
    monkeypatch.setattr("canvas_cli.config.RECEIPTS_DIR", receipts)
    monkeypatch.setattr("canvas_cli.receipts.RECEIPTS_DIR", receipts)
    return receipts


@pytest.fixture
def fake_course_resolver(monkeypatch):
    """Bypass Canvas API-backed course resolution in tests."""
    def _fake(identifier: str) -> str:
        fake_map = {
            "TEST101": "99999",
            "TEST202": "88888",
        }
        if identifier.isdigit():
            return identifier
        return fake_map.get(identifier.upper(), identifier)
    monkeypatch.setattr("canvas_cli.submit.resolve_course", _fake)


@pytest.fixture
def assignment_text():
    return json.loads((FIXTURES / "assignment_text.json").read_text())


@pytest.fixture
def assignment_upload():
    return json.loads((FIXTURES / "assignment_upload.json").read_text())


@pytest.fixture
def assignment_url():
    return json.loads((FIXTURES / "assignment_url.json").read_text())


@pytest.fixture
def assignment_submitted():
    return json.loads((FIXTURES / "assignment_submitted.json").read_text())


@pytest.fixture
def assignment_late():
    return json.loads((FIXTURES / "assignment_late.json").read_text())


@pytest.fixture
def assignment_locked():
    return json.loads((FIXTURES / "assignment_locked.json").read_text())


@pytest.fixture
def sample_text_file(tmp_path):
    f = tmp_path / "submission.md"
    f.write_text("<p>A test submission body.</p>")
    return f


@pytest.fixture
def sample_notebook(tmp_path):
    f = tmp_path / "lab.ipynb"
    f.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    return f


@pytest.fixture
def empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.touch()
    return f
