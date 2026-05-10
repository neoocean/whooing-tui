"""filters.py 단위 테스트 — 컬럼별 필터 / outside_paren_keywords / date_head."""

from __future__ import annotations

import pytest

from whooing_tui.filters import (
    FILTERABLE_COLUMNS,
    date_head,
    filter_entries,
    outside_paren_keywords,
)


# ---- date_head --------------------------------------------------------


def test_date_head_yyyymmdd():
    assert date_head("20260510") == "20260510"


def test_date_head_strips_sub_index():
    assert date_head("20260510.0001") == "20260510"
    assert date_head("20260510.999") == "20260510"


def test_date_head_empty_and_none():
    assert date_head(None) == ""
    assert date_head("") == ""


def test_date_head_passthrough_unrecognized():
    """8자리가 아니어도 그대로 (split 결과). _fmt_date 의 fallback 과 동일."""
    assert date_head("2026") == "2026"
    assert date_head("abcd") == "abcd"


# ---- outside_paren_keywords -------------------------------------------


@pytest.mark.parametrize("item, expected", [
    ("스타벅스(커피)", {"스타벅스"}),
    ("외식(저녁, 불고기)", {"외식"}),
    ("월급", {"월급"}),
    ("교통(버스) 주차", {"교통", "주차"}),
    ("커피, 빵", {"커피", "빵"}),
    ("", set()),
    (None, set()),
    # 괄호만 있는 경우 — 바깥 키워드 0
    ("(detail)", set()),
    # 양 끝 공백
    ("  외식  ", {"외식"}),
])
def test_outside_paren_keywords(item, expected):
    assert outside_paren_keywords(item) == expected


def test_outside_paren_keywords_returns_set_type():
    """set 객체라야 intersection 연산이 자연스럽다."""
    out = outside_paren_keywords("스타벅스")
    assert isinstance(out, set)


# ---- FILTERABLE_COLUMNS 명세 ------------------------------------------


def test_filterable_columns_constant():
    """date / left / right / item 만 — 사용자 명시 4개."""
    assert FILTERABLE_COLUMNS == ("date", "left", "right", "item")


# ---- filter_entries ---------------------------------------------------


def _entries():
    """공통 sample — 7건. date / left / right / item 별 분포 검증용."""
    return [
        {
            "entry_id": "e1", "entry_date": "20260510.0001",
            "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스(커피)",
        },
        {
            "entry_id": "e2", "entry_date": "20260510.0002",
            "l_account_id": "x20", "r_account_id": "x12",
            "item": "외식(저녁)",
        },
        {
            "entry_id": "e3", "entry_date": "20260510",
            "l_account_id": "x21", "r_account_id": "x11",
            "item": "버스",
        },
        {
            "entry_id": "e4", "entry_date": "20260509",
            "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스",  # 괄호 없음
        },
        {
            "entry_id": "e5", "entry_date": "20260509",
            "l_account_id": "x21", "r_account_id": "x12",
            "item": "지하철",
        },
        {
            "entry_id": "e6", "entry_date": "20260508",
            "l_account_id": "x21", "r_account_id": "x11",
            "item": "택시",
        },
        {
            "entry_id": "e7", "entry_date": "20260508",
            "l_account_id": "x20", "r_account_id": "x11",
            "item": "외식(점심)",
        },
    ]


def _ids(entries):
    return [e["entry_id"] for e in entries]


def test_filter_by_date_matches_same_day_ignoring_sub_index():
    es = _entries()
    target = es[0]  # 20260510.0001
    out = filter_entries(es, "date", target)
    # e1, e2, e3 모두 20260510 일자 (sub-index 무시)
    assert _ids(out) == ["e1", "e2", "e3"]


def test_filter_by_left_matches_same_l_account_id():
    es = _entries()
    target = es[0]  # x20
    out = filter_entries(es, "left", target)
    # e1, e2, e4, e7 의 l_account_id = x20
    assert set(_ids(out)) == {"e1", "e2", "e4", "e7"}


def test_filter_by_right_matches_same_r_account_id():
    es = _entries()
    target = es[2]  # x11
    out = filter_entries(es, "right", target)
    # e1, e3, e4, e6, e7 의 r_account_id = x11
    assert set(_ids(out)) == {"e1", "e3", "e4", "e6", "e7"}


def test_filter_by_item_matches_outside_paren_keyword():
    es = _entries()
    target = es[0]  # "스타벅스(커피)" → {"스타벅스"}
    out = filter_entries(es, "item", target)
    # e1 (스타벅스), e4 (스타벅스 — 괄호 없는 형태) 매칭. 다른 entries 는
    # "스타벅스" 키워드를 바깥에 갖지 않음.
    assert set(_ids(out)) == {"e1", "e4"}


def test_filter_by_item_with_multi_keyword_target():
    """target item 이 여러 키워드면 그 중 하나라도 매칭하는 entries."""
    es = [
        {"entry_id": "a1", "item": "교통 주차"},
        {"entry_id": "a2", "item": "주차장"},  # "주차" 와 다른 단어
        {"entry_id": "a3", "item": "교통(버스)"},
        {"entry_id": "a4", "item": "주차"},
    ]
    target = {"item": "교통 주차"}  # → {"교통", "주차"}
    out = filter_entries(es, "item", target)
    # a1 ("교통") + a3 ("교통") + a4 ("주차"). a2 는 "주차장" — 다른 단어.
    assert set(_ids(out)) == {"a1", "a3", "a4"}


def test_filter_by_item_empty_target_returns_empty():
    """괄호 바깥 키워드가 없는 target 은 매칭 불가 — 빈 list 반환."""
    es = _entries()
    target = {"item": "(전체 안만 있음)"}
    out = filter_entries(es, "item", target)
    assert out == []


def test_filter_unsupported_column_returns_empty():
    """money / memo 등 미지원 column 은 빈 list (호출자 책임)."""
    es = _entries()
    assert filter_entries(es, "money", es[0]) == []
    assert filter_entries(es, "memo", es[0]) == []
    assert filter_entries(es, "unknown", es[0]) == []


def test_filter_by_left_missing_target_id_returns_empty():
    es = _entries()
    target = {"l_account_id": ""}
    assert filter_entries(es, "left", target) == []
    assert filter_entries(es, "left", {}) == []


def test_filter_returns_subset_not_mutating_input():
    """입력 list 를 변형하지 않고 새 list 반환."""
    es = _entries()
    es_copy = list(es)
    filter_entries(es, "date", es[0])
    assert es == es_copy
