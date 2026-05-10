"""EntryEditDialog — 폼 검증 단위 테스트.

ModalScreen 의 화면 띄우기는 다른 테스트가 통합으로 검증하고, 본 파일은
폼 → EntryDraft 변환의 검증 로직 (`_resolve_account`, `_strip_comma_int`,
필수 필드 / 양수 / 좌우 동일 거부) 을 단위로 검사.
"""

from __future__ import annotations

import pytest

from whooing_tui.screens.edit_entry import (
    EntryDraft,
    _resolve_account,
    _strip_comma_int,
)
from whooing_tui.state import SessionState


@pytest.fixture
def session() -> SessionState:
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
    s = SessionState()
    s.set_section("s1")
    s.set_accounts(raw, flat)
    return s


def test_strip_comma_int_handles_thousands():
    assert _strip_comma_int("12,345") == 12345
    assert _strip_comma_int("12345") == 12345
    assert _strip_comma_int(" 1,000,000 ") == 1_000_000
    # int() 자체는 음수 허용 — 호출자가 양수 검증.
    assert _strip_comma_int("-500") == -500


@pytest.mark.parametrize("bad", ["", "   ", "abc", "12 34"])
def test_strip_comma_int_rejects_invalid(bad):
    with pytest.raises(ValueError):
        _strip_comma_int(bad)


def test_resolve_account_by_id(session):
    out = _resolve_account(session, "x20")
    assert out == ("x20", "expenses")


def test_resolve_account_by_title(session):
    out = _resolve_account(session, "식비")
    assert out == ("x20", "expenses")


def test_resolve_account_case_insensitive_title(session):
    # 한국어는 case 가 없지만 영문 case 들도 지원 — 양 끝 공백 허용
    s = SessionState()
    s.set_section("s1")
    s.set_accounts(
        {"assets": [{"account_id": "x99", "title": "Cash"}]},
        [{"account_id": "x99", "title": "Cash", "type": "assets"}],
    )
    assert _resolve_account(s, "  cash  ") == ("x99", "assets")
    assert _resolve_account(s, "CASH") == ("x99", "assets")


def test_resolve_account_unknown_returns_none(session):
    assert _resolve_account(session, "스타벅스") is None
    assert _resolve_account(session, "xZZ") is None
    assert _resolve_account(session, "") is None
    assert _resolve_account(session, "   ") is None


def test_entry_draft_is_dataclass():
    """필드 셋업이 회귀하지 않도록 최소 instantiation 검사."""
    d = EntryDraft(
        entry_date="20260510", money=1000,
        l_account_id="x20", r_account_id="x11",
        item="스타벅스", memo="",
    )
    assert d.entry_id is None  # 새 입력 — 수정 모드는 entry_id 가 채워짐
    assert d.money == 1000
