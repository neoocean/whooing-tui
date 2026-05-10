"""EntryEditDialog — 폼 normalize / 검증 단위 테스트.

ModalScreen 의 화면 띄우기는 통합 테스트가 검증하고, 본 파일은 helper
함수들 (`_strip_comma_int`, `_format_date_dashed`, `_format_money_comma`,
`_parse_dashed_date_to_yyyymmdd`, `parse_hashtags_input`, `_digits_only`)
의 정규화 / 거부 / 등치 케이스를 단위로 검사.

CL #51076 에서 free-text account 매칭은 사라지고 picker 모달로 대체됐다 —
관련 단위 테스트는 picker 통합 테스트가 대신 한다.
"""

from __future__ import annotations

import pytest

from whooing_tui.screens.edit_entry import (
    EntryDraft,
    _digits_only,
    _format_date_dashed,
    _format_money_comma,
    _parse_dashed_date_to_yyyymmdd,
    _strip_comma_int,
    parse_hashtags_input,
)


# ---- _strip_comma_int -------------------------------------------------


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


# ---- _digits_only -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-05-09", "20260509"),
        ("1,234,567", "1234567"),
        ("abc 123", "123"),
        ("", ""),
        ("---", ""),
    ],
)
def test_digits_only(raw, expected):
    assert _digits_only(raw) == expected


def test_digits_only_truncates_to_max_len():
    assert _digits_only("202605091234", max_len=8) == "20260509"
    assert _digits_only("12", max_len=8) == "12"


# ---- _format_date_dashed ----------------------------------------------


@pytest.mark.parametrize(
    "digits,expected",
    [
        ("", ""),
        ("2", "2"),
        ("2026", "2026"),
        ("20265", "2026-5"),
        ("202605", "2026-05"),
        ("2026059", "2026-05-9"),
        ("20260509", "2026-05-09"),
    ],
)
def test_format_date_dashed_progressive(digits, expected):
    assert _format_date_dashed(digits) == expected


def test_format_date_dashed_ignores_extra_digits():
    # 8자리까지만 의미 있다 — 추가 입력은 그대로 표시 (호출 측이 자르도록 둠)
    assert _format_date_dashed("20260509999") == "2026-05-09"


# ---- _format_money_comma ----------------------------------------------


@pytest.mark.parametrize(
    "digits,expected",
    [
        ("", ""),
        ("0", "0"),
        ("123", "123"),
        ("1234", "1,234"),
        ("1234567", "1,234,567"),
    ],
)
def test_format_money_comma(digits, expected):
    assert _format_money_comma(digits) == expected


# ---- _parse_dashed_date_to_yyyymmdd -----------------------------------


def test_parse_dashed_date_accepts_dashed_or_compact():
    assert _parse_dashed_date_to_yyyymmdd("2026-05-09") == "20260509"
    assert _parse_dashed_date_to_yyyymmdd("20260509") == "20260509"


def test_parse_dashed_date_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_dashed_date_to_yyyymmdd("not-a-date")


# ---- parse_hashtags_input ---------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("   ", []),
        ("식비", ["식비"]),
        ("#식비 #저녁", ["식비", "저녁"]),
        ("식비, 저녁, 데이트", ["식비", "저녁", "데이트"]),
        ("#식비, 식비 저녁", ["식비", "저녁"]),  # dedup
        ("  #여행#일본  ", ["여행", "일본"]),  # `#` 도 분리자
    ],
)
def test_parse_hashtags_input(raw, expected):
    assert parse_hashtags_input(raw) == expected


# ---- EntryDraft -------------------------------------------------------


def test_entry_draft_default_fields():
    """새 필드 (l_type / r_type / tags) 기본값이 안전한지 회귀 검사."""
    d = EntryDraft(
        entry_date="20260510", money=1000,
        l_account_id="x20", r_account_id="x11",
    )
    assert d.entry_id is None
    assert d.money == 1000
    assert d.l_type == "" and d.r_type == ""
    assert d.tags == []  # mutable default 가 dataclass field 로 안전한지 확인
    assert d.item == "" and d.memo == ""


def test_entry_draft_preserves_tags():
    d = EntryDraft(
        entry_date="20260510", money=1000,
        l_account_id="x20", r_account_id="x11",
        l_type="expenses", r_type="assets",
        tags=["식비", "저녁"],
    )
    assert d.tags == ["식비", "저녁"]
    assert d.l_type == "expenses"
