"""whooing_tui.constants 의 값 + 의미 sanity tests.

CL #52858+. 매직 상수가 의도된 값에서 *조용히* 변하지 않도록 회귀 방어 +
의미적 invariant 검증.
"""

from __future__ import annotations

from whooing_tui import constants as C


def test_window_days_are_consistent():
    assert C.MIN_WINDOW_DAYS == 1
    assert C.DEFAULT_WINDOW_DAYS > C.MIN_WINDOW_DAYS
    assert C.MAX_WINDOW_DAYS > C.DEFAULT_WINDOW_DAYS
    assert C.WINDOW_STEP_DAYS > 0


def test_hangul_codepoints():
    # Hangul Syllables block 의 표준 경계.
    assert C.HANGUL_SYLLABLE_FIRST == 0xAC00
    assert C.HANGUL_SYLLABLE_LAST == 0xD7A3


def test_filter_expand_step_months_strictly_increasing():
    """필터 점진 확장 step 은 *과거로 멀리* 가야 의미 있음 — 단조 증가."""
    months = C.FILTER_EXPAND_STEP_MONTHS
    assert len(months) >= 2
    for a, b in zip(months, months[1:]):
        assert a < b, f"step 단조 증가 위반: {months}"


def test_filter_expand_reaches_at_least_five_years():
    """CL #52858+ 사용자 요청: 필터 확장이 최소 5 년 (60 개월) 이상 도달."""
    months = C.FILTER_EXPAND_STEP_MONTHS
    assert months[-1] >= 60, (
        f"필터 확장 최대 step 이 5 년 미만: {months[-1]} 개월. "
        f"종전 default (24 개월) 회귀 가능성."
    )


def test_filter_expand_step_months_match_max_window():
    """필터 확장 최대 step (개월) 이 MAX_WINDOW_DAYS (일) 와 대체로 일관."""
    last_months = C.FILTER_EXPAND_STEP_MONTHS[-1]
    approx_days = last_months * 30
    # 7% 이내로 일치 — 365*5 = 1825 vs 60*30 = 1800.
    assert abs(approx_days - C.MAX_WINDOW_DAYS) / C.MAX_WINDOW_DAYS < 0.07


def test_p4_timeouts_sane():
    assert C.P4_LIGHT_PREVIEW_TIMEOUT_SEC < C.P4_DEFAULT_TIMEOUT_SEC
    assert C.P4_PENDING_JOIN_TIMEOUT_SEC >= C.P4_DEFAULT_TIMEOUT_SEC


def test_compact_thresholds_descending():
    """컴팩트 단계 — 좁은 터미널일수록 더 많은 컬럼 숨김. 임계값 descending."""
    th = C.COMPACT_THRESHOLDS
    assert len(th) == 4
    for a, b in zip(th, th[1:]):
        assert a > b, f"compact threshold descending 위반: {th}"
