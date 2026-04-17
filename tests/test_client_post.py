"""Client POST helpers tests (mocked httpx at the transport layer)."""

from urllib.parse import parse_qsl

import httpx
import pytest
import respx

from canvas_cli.client import (
    _extract_csrf_token,
    make_request,
    post,
    post_form,
)


def test_make_request_rejects_both_json_and_form():
    """json_body and form_data must be mutually exclusive."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        make_request("post", "/x", json_body={"a": 1}, form_data={"b": 2})


# --- CSRF extraction ---

def test_extract_csrf_token_from_cookie_header():
    header = "canvas_session=abc; _csrf_token=xyz123; _ga=ga_val"
    assert _extract_csrf_token(header) == "xyz123"


def test_extract_csrf_token_url_decodes():
    """Canvas stores the token URL-encoded; we must decode before sending."""
    header = "_csrf_token=abc%2B%2Fdef%3D"
    assert _extract_csrf_token(header) == "abc+/def="


def test_extract_csrf_token_missing_returns_none():
    header = "canvas_session=abc; _ga=ga_val"
    assert _extract_csrf_token(header) is None


def test_extract_csrf_token_empty_value_returns_none():
    header = "_csrf_token="
    assert _extract_csrf_token(header) is None


# --- POST sends CSRF header ---

@respx.mock
def test_post_sends_csrf_header(monkeypatch):
    monkeypatch.setattr(
        "canvas_cli.client.load_cookies",
        lambda: "canvas_session=abc; _csrf_token=mytoken123",
    )
    route = respx.post("https://canvas.test/api/v1/x").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    post("/x", json_body={"k": "v"})
    assert route.called
    sent = route.calls.last.request
    assert sent.headers.get("X-CSRF-Token") == "mytoken123"


@respx.mock
def test_get_does_not_send_csrf_header(monkeypatch):
    """CSRF is only for state-changing methods. GET must not send it."""
    monkeypatch.setattr(
        "canvas_cli.client.load_cookies",
        lambda: "canvas_session=abc; _csrf_token=mytoken123",
    )
    route = respx.get("https://canvas.test/api/v1/x").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    from canvas_cli.client import get
    get("/x")
    assert route.called
    assert "X-CSRF-Token" not in route.calls.last.request.headers


@respx.mock
def test_post_without_csrf_cookie_omits_header(monkeypatch):
    """Missing _csrf_token cookie doesn't crash — we just don't send the header."""
    monkeypatch.setattr(
        "canvas_cli.client.load_cookies",
        lambda: "canvas_session=abc",
    )
    route = respx.post("https://canvas.test/api/v1/x").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    post("/x", json_body={"k": "v"})
    assert route.called
    assert "X-CSRF-Token" not in route.calls.last.request.headers


# --- Form encoding ---

@respx.mock
def test_post_form_urlencodes_body_with_content_type(monkeypatch):
    monkeypatch.setattr(
        "canvas_cli.client.load_cookies",
        lambda: "canvas_session=abc; _csrf_token=t",
    )
    route = respx.post("https://canvas.test/api/v1/x").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    post_form("/x", [
        ("submission[submission_type]", "online_text_entry"),
        ("submission[body]", "<p>hello</p>"),
    ])
    assert route.called
    req = route.calls.last.request
    assert req.headers.get("Content-Type") == "application/x-www-form-urlencoded"
    body = req.content.decode("utf-8")
    pairs = dict(parse_qsl(body))
    assert pairs["submission[submission_type]"] == "online_text_entry"
    assert pairs["submission[body]"] == "<p>hello</p>"


@respx.mock
def test_post_form_handles_repeated_keys(monkeypatch):
    """file_ids[]=1&file_ids[]=2 requires list-of-tuples + doseq encoding."""
    monkeypatch.setattr(
        "canvas_cli.client.load_cookies",
        lambda: "canvas_session=abc; _csrf_token=t",
    )
    route = respx.post("https://canvas.test/api/v1/x").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    post_form("/x", [
        ("submission[submission_type]", "online_upload"),
        ("submission[file_ids][]", "1"),
        ("submission[file_ids][]", "2"),
    ])
    assert route.called
    body = route.calls.last.request.content.decode("utf-8")
    # parse_qsl gives us all repeated values
    pairs = parse_qsl(body)
    file_ids = [v for k, v in pairs if k == "submission[file_ids][]"]
    assert file_ids == ["1", "2"]
