"""TypedDict 응답 스키마 smoke tests (CL #52834+).

TypedDict 는 런타임 검증을 하지 않으므로 본 테스트는 *import / 타입
구조 / 라이브러리 호환* 만 검증. 실 데이터 매칭은 client.py 의 단위
테스트가 담당.
"""

from __future__ import annotations

from whooing_tui.responses import (
    AccountDict,
    AccountsByType,
    CreateEntryResponse,
    EntryDict,
    SectionDict,
)


def test_entry_dict_typed_dict_is_constructible():
    entry: EntryDict = {
        "entry_id": "e1",
        "entry_date": "20260518",
        "money": 12000,
        "l_account": "expenses",
        "l_account_id": "x20",
        "r_account": "assets",
        "r_account_id": "x11",
    }
    assert entry["entry_id"] == "e1"
    assert entry["money"] == 12000


def test_entry_dict_accepts_optional_memo_and_item():
    entry: EntryDict = {
        "entry_id": "e2", "entry_date": "20260518", "money": 1,
        "l_account": "x", "l_account_id": "1",
        "r_account": "y", "r_account_id": "2",
        "item": "스타벅스", "memo": "회의비",
    }
    assert entry.get("memo") == "회의비"


def test_section_dict_minimal():
    section: SectionDict = {"section_id": "s1", "title": "main"}
    assert section["title"] == "main"


def test_account_dict_minimal():
    acc: AccountDict = {"account_id": "x11", "title": "현금"}
    assert acc["account_id"] == "x11"


def test_accounts_by_type_empty():
    """모든 type 이 NotRequired — 빈 dict 도 valid."""
    by_type: AccountsByType = {}
    assert by_type == {}


def test_accounts_by_type_partial():
    by_type: AccountsByType = {
        "assets": [{"account_id": "x11", "title": "현금"}],
    }
    assert "assets" in by_type
    assert "liabilities" not in by_type


def test_create_entry_response_shapes():
    """`_extract_entry_id` 가 보는 변형 — 모두 NotRequired."""
    a: CreateEntryResponse = {"entry_id": "new1"}
    assert a.get("entry_id") == "new1"
    b: CreateEntryResponse = {
        "entries": [{
            "entry_id": "new2", "entry_date": "20260518", "money": 1,
            "l_account": "x", "l_account_id": "1",
            "r_account": "y", "r_account_id": "2",
        }],
    }
    assert b.get("entries", [{}])[0]["entry_id"] == "new2"
