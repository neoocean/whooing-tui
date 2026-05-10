"""entries_compact — pure helper 단위 테스트.

CL #51158+ (review C4). 종전 entries.py 의 classmethod 였던 약어 / 임계값
/ visibility 가 module-level 함수로 이동. 본 파일은 그 helpers 만 단위 검증.
EntriesScreen 통합 (resize / format_cell) 은 test_narrow_terminal.py 가 담당.
"""

from __future__ import annotations

import pytest

from whooing_tui.screens.entries_compact import (
    COMPACT_THRESHOLDS,
    abbreviate_account_name,
    column_is_visible,
    compute_compact_level,
    hidden_columns_for_level,
    is_hangul,
)


# ---- compute_compact_level ----------------------------------------------


@pytest.mark.parametrize("width, expected", [
    (200, 0),
    (80, 0),       # 임계값 같음 — 0.
    (79, 1),
    (60, 1),
    (59, 2),
    (45, 2),
    (44, 3),
    (35, 3),
    (34, 4),
    (10, 4),
])
def test_compute_compact_level(width, expected):
    assert compute_compact_level(width) == expected


def test_compact_thresholds_are_descending():
    """단계 함수가 break 로 효율 — 내림차순 보장."""
    for i in range(len(COMPACT_THRESHOLDS) - 1):
        assert COMPACT_THRESHOLDS[i] > COMPACT_THRESHOLDS[i + 1]


# ---- is_hangul ----------------------------------------------------------


@pytest.mark.parametrize("ch, expected", [
    ("가", True),
    ("힣", True),
    ("스", True),
    ("A", False),
    ("1", False),
    ("!", False),
    ("ㄱ", False),    # 자모 자체 (NOT 음절).
    ("", False),
])
def test_is_hangul(ch, expected):
    assert is_hangul(ch) == expected


# ---- abbreviate_account_name -------------------------------------------


@pytest.mark.parametrize("name, expected", [
    # 기본 한국식.
    ("스타벅스", "스벅"),
    ("맥도날드", "맥날"),
    ("삼성전자", "삼전"),
    ("롯데리아", "롯리"),
    # 짧은.
    ("식비", "식비"),
    ("교통비", "교통"),
    ("현금", "현금"),
    # 긴 (5+) — fallback.
    ("현대자동차", "현대"),
    ("국민건강보험", "국민"),
    # 회사 prefix strip.
    ("(주)스타벅스", "스벅"),
    ("주식회사 카카오", "카카"),
    # 회사 suffix strip.
    ("스타벅스코리아", "스벅"),
    ("(주)스타벅스코리아", "스벅"),
    ("삼성그룹", "삼성"),
    ("CJ글로벌", "CJ"),
    ("롯데홀딩스", "롯데"),
    # suffix 만 남으면 strip 안 함.
    ("X그룹", "X그"),
    # 일반 괄호 (회사 prefix 가 아닌).
    ("[자산]현금", "자현"),
    ("「인용」현금", "인현"),
    # 영문 — 단순 [:2].
    ("Starbucks", "St"),
    ("BC카드", "BC"),
    # 빈 / None.
    ("", ""),
])
def test_abbreviate_account_name_korean_rules(name, expected):
    assert abbreviate_account_name(name) == expected


def test_abbreviate_handles_none():
    """None 입력도 안전 — 빈 문자열 반환."""
    assert abbreviate_account_name(None) == ""  # type: ignore[arg-type]


# ---- hidden_columns_for_level / column_is_visible ----------------------


def test_hidden_columns_per_level():
    assert hidden_columns_for_level(0) == set()
    assert hidden_columns_for_level(1) == {5}     # memo
    assert hidden_columns_for_level(2) == {5}     # memo (left/right 약어로 visible)
    assert hidden_columns_for_level(3) == {3, 5}  # right + memo
    assert hidden_columns_for_level(4) == {2, 3, 5}  # all 3


def test_column_is_visible():
    # level 0 — 모두 visible.
    for c in range(6):
        assert column_is_visible(c, 0)
    # level 4 — date / money / item 만.
    assert column_is_visible(0, 4)
    assert column_is_visible(1, 4)
    assert not column_is_visible(2, 4)
    assert not column_is_visible(3, 4)
    assert column_is_visible(4, 4)
    assert not column_is_visible(5, 4)
