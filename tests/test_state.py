"""SessionState — 섹션 전환 시 캐시 무효화 + account_id ↔ title 인덱스."""

from __future__ import annotations

from whooing_tui.state import SessionState


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
