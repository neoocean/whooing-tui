"""errors.map_response / sanitize_for_log 검증."""

from __future__ import annotations

from whooing_tui.errors import map_response, sanitize_for_log, sanitize_token
from whooing_tui.models import ToolError


def test_map_response_400_is_user_input():
    e = map_response(400, "잘못된 파라미터", {"error_parameters": {"date": "bad"}})
    assert isinstance(e, ToolError)
    assert e.kind == "USER_INPUT"
    assert "date" in str(e.details.get("error_parameters") or {})


def test_map_response_401_is_auth():
    e = map_response(401, "expired")
    assert e.kind == "AUTH"


def test_map_response_405_is_auth():
    e = map_response(405)
    assert e.kind == "AUTH"


def test_map_response_402_is_daily_rate_limit():
    e = map_response(402, body={"rest_of_api": 0})
    assert e.kind == "RATE_LIMIT"
    assert e.details.get("rest_of_api") == 0


def test_map_response_429_is_minute_rate_limit():
    e = map_response(429)
    assert e.kind == "RATE_LIMIT"
    assert e.details.get("rest_of_api") is None


def test_map_response_5xx_is_upstream():
    e = map_response(503, "service unavailable")
    assert e.kind == "UPSTREAM"


def test_map_response_unknown_is_upstream():
    e = map_response(418, "I'm a teapot")
    assert e.kind == "UPSTREAM"


def test_sanitize_for_log_masks_secret_keys():
    obj = {
        "section_id": "s1",
        "title": "main",
        "webhook_token": "supersecret",
        "nested": {"api_key": "abc", "ok": "value"},
    }
    cleaned = sanitize_for_log(obj)
    assert cleaned["section_id"] == "s1"
    assert cleaned["title"] == "main"
    assert cleaned["webhook_token"] == "***masked***"
    assert cleaned["nested"]["api_key"] == "***masked***"
    assert cleaned["nested"]["ok"] == "value"
    # 원본은 그대로
    assert obj["webhook_token"] == "supersecret"


def test_sanitize_for_log_handles_lists():
    obj = [{"token": "x"}, {"value": "y"}]
    cleaned = sanitize_for_log(obj)
    assert cleaned[0]["token"] == "***masked***"
    assert cleaned[1]["value"] == "y"


def test_sanitize_token_short_and_long():
    assert sanitize_token("") == "***empty***"
    assert sanitize_token("abc") == "***short***"
    long = "__eyJhabcdefghij9876"
    out = sanitize_token(long)
    assert "9876" in out
    assert "abcdef" not in out
