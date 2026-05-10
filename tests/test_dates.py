"""KST 날짜 유틸 검증."""

from __future__ import annotations

import pytest

from whooing_tui.dates import (
    date_diff_days,
    days_ago_yyyymmdd,
    parse_yyyymm,
    parse_yyyymmdd,
    split_yearly_ranges,
    today_yyyymmdd,
)


def test_today_format():
    s = today_yyyymmdd()
    assert len(s) == 8
    assert s.isdigit()


def test_days_ago_negative_rejected():
    with pytest.raises(ValueError):
        days_ago_yyyymmdd(-1)


def test_days_ago_zero_is_today():
    assert days_ago_yyyymmdd(0) == today_yyyymmdd()


def test_parse_yyyymmdd_valid():
    assert parse_yyyymmdd("20260510") == "20260510"


@pytest.mark.parametrize("bad", ["2026051", "2026-05-10", "20260230", "abcd0510", ""])
def test_parse_yyyymmdd_invalid(bad):
    with pytest.raises(ValueError):
        parse_yyyymmdd(bad)


def test_parse_yyyymm_valid():
    assert parse_yyyymm("202605") == "202605"


@pytest.mark.parametrize("bad", ["20260", "202613", "abc605", "2026-05"])
def test_parse_yyyymm_invalid(bad):
    with pytest.raises(ValueError):
        parse_yyyymm(bad)


def test_date_diff_days():
    assert date_diff_days("20260510", "20260510") == 0
    assert date_diff_days("20260510", "20260520") == 10
    # 절대값
    assert date_diff_days("20260520", "20260510") == 10


def test_split_yearly_ranges_short():
    rs = split_yearly_ranges("20260101", "20260601")
    assert rs == [("20260101", "20260601")]


def test_split_yearly_ranges_long():
    rs = split_yearly_ranges("20240101", "20260101")
    # 2년치라면 2~3 청크로 분할 (정확한 끝 포함)
    assert len(rs) >= 2
    # 첫 청크는 시작 일자
    assert rs[0][0] == "20240101"
    # 마지막 청크는 끝 일자에 정확히 맞물림
    assert rs[-1][1] == "20260101"
    # 인접 청크 사이는 1일 간격
    for i in range(len(rs) - 1):
        end_i = rs[i][1]
        start_next = rs[i + 1][0]
        # 종료 다음 날 == 다음 시작
        assert int(start_next) > int(end_i)
