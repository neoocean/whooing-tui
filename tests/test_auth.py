"""WhooingAuth — 헤더 빌드 + 토큰 마스크 검증."""

from __future__ import annotations

import pytest

from whooing_tui.auth import WhooingAuth, load_auth_from_env


def test_headers_uses_x_api_key():
    auth = WhooingAuth(token="__eyJhsomething_secret_xxxx9876")
    h = auth.headers()
    assert h == {"X-API-Key": "__eyJhsomething_secret_xxxx9876"}


def test_repr_masks_token_to_last4():
    auth = WhooingAuth(token="__eyJhsomething_secret_xxxx9876")
    rep = repr(auth)
    # 마지막 4자만 노출, 나머지는 마스크
    assert "9876" in rep
    assert "secret" not in rep
    assert "***" in rep


def test_repr_masks_short_token_completely():
    # 12자 이하면 hint 도 안 남김
    auth = WhooingAuth(token="abc")
    rep = repr(auth)
    assert "abc" not in rep
    assert "***" in rep


def test_str_equals_repr():
    auth = WhooingAuth(token="__eyJh1234567890")
    assert str(auth) == repr(auth)


def test_load_auth_from_env_missing(monkeypatch):
    monkeypatch.delenv("WHOOING_AI_TOKEN", raising=False)
    with pytest.raises(ValueError, match="WHOOING_AI_TOKEN 미설정"):
        load_auth_from_env()


def test_load_auth_from_env_placeholder(monkeypatch):
    monkeypatch.setenv("WHOOING_AI_TOKEN", "__eyJh...")
    with pytest.raises(ValueError, match="placeholder"):
        load_auth_from_env()


def test_load_auth_from_env_real(monkeypatch):
    monkeypatch.setenv(
        "WHOOING_AI_TOKEN",
        "__eyJhcHBfaWQiOjMsInRva2VuIjoiYWJjZGVmZ2hpamtsbW5vcA==",
    )
    auth = load_auth_from_env()
    assert auth.token.startswith("__eyJh")
    assert auth.headers()["X-API-Key"].startswith("__eyJh")
