"""SessionState — 섹션 전환 시 캐시 무효화 + account_id ↔ title 인덱스.

CL #51031+ 부터 영구 사용자 상태 (`load_state` / `save_state` /
`load_last_section_id` / `save_last_section_id`) 도 본 모듈에 들어가
같이 검증.
"""

from __future__ import annotations

import json

import pytest

from whooing_tui import state as state_mod
from whooing_tui.state import (
    SessionState,
    load_last_section_id,
    load_state,
    save_last_section_id,
    save_state,
)


def _accounts():
    raw = {
        "assets": [{"account_id": "x11", "title": "현금"}],
        "expenses": [
            {"account_id": "x20", "title": "식비"},
            {"account_id": "x21", "title": "교통비"},
        ],
    }
    flat = [
        {"account_id": "x11", "title": "현금", "type": "assets"},
        {"account_id": "x20", "title": "식비", "type": "expenses"},
        {"account_id": "x21", "title": "교통비", "type": "expenses"},
    ]
    return raw, flat


def test_set_section_then_accounts():
    s = SessionState()
    s.set_section("s1", "main")
    raw, flat = _accounts()
    s.set_accounts(raw, flat)
    assert s.section_id == "s1"
    assert s.section_title == "main"
    assert s.title_of("x20") == "식비"
    assert s.id_of("식비") == "x20"
    # 대소문자 무시 (영문 케이스에 대비)
    assert s.id_of("식비".upper()) == "x20"


def test_change_section_invalidates_accounts():
    s = SessionState()
    raw, flat = _accounts()
    s.set_section("s1")
    s.set_accounts(raw, flat)
    assert s.id_of("식비") == "x20"

    s.set_section("s2")
    # 캐시가 비워져야 함
    assert s.accounts_raw == {}
    assert s.accounts_flat == []
    assert s.id_of("식비") is None
    assert s.title_of("x20") == "x20"  # fallback to id


def test_title_of_unknown_returns_id():
    s = SessionState()
    assert s.title_of("xZZ") == "xZZ"


# ---- 영구 사용자 상태 (state.json) -------------------------------------


@pytest.fixture
def isolated_state_home(monkeypatch, tmp_path):
    """`$XDG_CONFIG_HOME` 을 tmp_path 로 격리해 실 사용자 home 을 안 만지게."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


def test_load_state_missing_returns_empty(isolated_state_home):
    assert load_state() == {}


def test_save_then_load_state_roundtrip(isolated_state_home):
    save_state({"last_section_id": "s9046", "version": 1})
    out = load_state()
    assert out == {"last_section_id": "s9046", "version": 1}


def test_save_state_writes_atomic(isolated_state_home, tmp_path):
    save_state({"last_section_id": "s1"})
    p = state_mod._state_path()
    assert p.is_file()
    # `.tmp` 임시 파일은 rename 후 사라져야 함
    assert not p.with_suffix(p.suffix + ".tmp").exists()
    # JSON 형태 검증
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["last_section_id"] == "s1"


def test_load_state_corrupted_returns_empty(isolated_state_home):
    p = state_mod._state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not json {", encoding="utf-8")
    assert load_state() == {}


def test_load_state_non_dict_returns_empty(isolated_state_home):
    p = state_mod._state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_state() == {}


def test_save_last_section_id_persists(isolated_state_home):
    save_last_section_id("s9046")
    assert load_last_section_id() == "s9046"


def test_save_last_section_id_skip_when_unchanged(isolated_state_home):
    save_last_section_id("s9046")
    p = state_mod._state_path()
    mtime1 = p.stat().st_mtime_ns
    save_last_section_id("s9046")  # 같은 값 — write skip
    mtime2 = p.stat().st_mtime_ns
    assert mtime1 == mtime2  # 파일 수정 시각이 그대로


def test_save_last_section_id_overwrites_old(isolated_state_home):
    save_last_section_id("s1")
    save_last_section_id("s2")
    assert load_last_section_id() == "s2"


def test_save_last_section_id_preserves_other_keys(isolated_state_home):
    save_state({"foo": "bar", "version": 1})
    save_last_section_id("s9046")
    out = load_state()
    assert out["foo"] == "bar"
    assert out["last_section_id"] == "s9046"


def test_load_last_section_id_empty_string_returns_none(isolated_state_home):
    save_state({"last_section_id": "", "version": 1})
    assert load_last_section_id() is None


def test_load_last_section_id_missing_returns_none(isolated_state_home):
    save_state({"version": 1})
    assert load_last_section_id() is None
