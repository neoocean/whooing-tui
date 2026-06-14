"""TOML 설정 로더 검증 — 임시 디렉토리에 파일을 만들고 $WHOOING_TUI_CONFIG 로 가리킨다."""

from __future__ import annotations

import textwrap

import pytest

from whooing_tui import config as config_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    config_mod.reset_cache()
    yield
    config_mod.reset_cache()


def test_default_when_no_file(tmp_path, monkeypatch):
    # 후보 경로 모두 존재하지 않게 강제 — 개발 머신의 실 project-root
    # `tui/whooing-tui.toml` 로 fall-through 하지 않도록 후보 목록 자체를 격리.
    monkeypatch.setattr(
        config_mod, "_candidate_paths", lambda: [tmp_path / "missing.toml"],
    )
    cfg = config_mod.load_config(force_reload=True)
    assert cfg.theme == "textual-dark"
    assert cfg.entries_page_size == 50
    assert cfg.default_window_days == 30
    assert cfg.sync_backend == "none"


def test_full_override(tmp_path, monkeypatch):
    p = tmp_path / "whooing-tui.toml"
    p.write_text(textwrap.dedent("""
        [ui]
        theme = "textual-light"
        entries_page_size = 100

        [entries]
        default_window_days = 7
    """).lstrip(), encoding="utf-8")
    monkeypatch.setenv("WHOOING_TUI_CONFIG", str(p))
    cfg = config_mod.load_config(force_reload=True)
    assert cfg.theme == "textual-light"
    assert cfg.entries_page_size == 100
    assert cfg.default_window_days == 7


def test_partial_uses_defaults_for_missing_keys(tmp_path, monkeypatch):
    p = tmp_path / "whooing-tui.toml"
    p.write_text("[ui]\ntheme = \"textual-light\"\n", encoding="utf-8")
    monkeypatch.setenv("WHOOING_TUI_CONFIG", str(p))
    cfg = config_mod.load_config(force_reload=True)
    assert cfg.theme == "textual-light"
    # 다른 키는 기본값 유지
    assert cfg.entries_page_size == 50
    assert cfg.default_window_days == 30


def test_malformed_falls_back_to_default(tmp_path, monkeypatch):
    p = tmp_path / "whooing-tui.toml"
    p.write_text("not toml = = =\n", encoding="utf-8")
    monkeypatch.setenv("WHOOING_TUI_CONFIG", str(p))
    cfg = config_mod.load_config(force_reload=True)
    assert cfg.theme == "textual-dark"  # default
