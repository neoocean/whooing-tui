"""반복 거래 누락 감지 (`whooing_core.recurring`) 단위 테스트.

pure 함수라 후잉 / sqlite 없이 entry dict list 만으로 검증.
"""

from __future__ import annotations

from whooing_core.recurring import (
    CADENCE_LABELS_KO,
    detect_recurring_omissions,
    find_recurring_series,
)


def _e(eid: str, d: str, money: int, item: str,
       l: str = "x20", r: str = "x11") -> dict:
    return {
        "entry_id": eid,
        "entry_date": d,
        "money": money,
        "item": item,
        "l_account_id": l,
        "r_account_id": r,
    }


def _monthly(item: str, months: list[str], *, day: str = "14",
             money: int = 9900, prefix: str = "m") -> list[dict]:
    return [
        _e(f"{prefix}{i}", f"2026{mm}{day}", money, item)
        for i, mm in enumerate(months)
    ]


# ---- 시리즈 추출 기본 --------------------------------------------------


def test_clean_monthly_series_has_no_missing():
    entries = _monthly("넷플릭스", ["01", "02", "03", "04", "05"])
    series = find_recurring_series(entries, as_of="20260520")
    assert len(series) == 1
    s = series[0]
    assert s.cadence == "monthly"
    assert s.occurrences == 5
    assert s.missing == ()
    assert s.typical_money == 9900
    # 누락 없으니 omission 결과에는 안 나온다.
    assert detect_recurring_omissions(entries, as_of="20260520") == []


def test_too_few_occurrences_is_not_a_series():
    entries = _monthly("커피정기", ["01", "02"])
    assert find_recurring_series(entries, as_of="20260401") == []


def test_irregular_dates_not_classified():
    # 간격 51 / 5 / 106 → 어떤 주기 band 에도 안 듦 → skip.
    entries = [
        _e("a", "20260105", 5000, "잡비"),
        _e("b", "20260225", 5000, "잡비"),
        _e("c", "20260302", 5000, "잡비"),
        _e("d", "20260616", 5000, "잡비"),
    ]
    assert find_recurring_series(entries, as_of="20260701") == []


# ---- 내부 gap 누락 -----------------------------------------------------


def test_internal_gap_detected():
    # 3월이 빠짐 — 재개됐으므로 gap 누락.
    entries = _monthly("월세", ["01", "02", "04", "05"], day="15", money=600000)
    flagged = detect_recurring_omissions(entries, as_of="20260520")
    assert len(flagged) == 1
    s = flagged[0]
    assert s.gap_count == 1
    assert s.missing[0].kind == "gap"
    assert s.missing[0].expected_date == "20260315"
    assert not s.has_overdue


def test_weekly_internal_gap():
    entries = [
        _e("a", "20260501", 3000, "주간모임"),
        _e("b", "20260508", 3000, "주간모임"),
        # 05-15 빠짐.
        _e("c", "20260522", 3000, "주간모임"),
        _e("d", "20260529", 3000, "주간모임"),
    ]
    flagged = detect_recurring_omissions(entries, as_of="20260602")
    assert len(flagged) == 1
    s = flagged[0]
    assert s.cadence == "weekly"
    assert [m.expected_date for m in s.missing] == ["20260515"]


# ---- 마지막 이후 연체 (overdue) ----------------------------------------


def test_overdue_trailing_detected():
    entries = _monthly("헬스장", ["01", "02", "03"], day="10", money=50000)
    # 04-10 이 기대됐으나 안 들어옴. as_of 가 충분히 지나야 연체.
    flagged = detect_recurring_omissions(entries, as_of="20260425")
    assert len(flagged) == 1
    s = flagged[0]
    assert s.has_overdue
    assert s.overdue_count == 1
    assert s.missing[0].expected_date == "20260410"
    assert s.missing[0].kind == "overdue"


def test_overdue_not_flagged_within_grace():
    # 04-10 기대, as_of 04-15 — 허용오차+유예(11일) 안이라 아직 연체 아님.
    entries = _monthly("헬스장", ["01", "02", "03"], day="10", money=50000)
    flagged = detect_recurring_omissions(entries, as_of="20260415")
    assert flagged == []


def test_discontinued_series_not_flagged():
    # 2025 초 3회 후 끊김. 2026-06 기준 연체가 3주기 초과 → 종료로 판단.
    entries = [
        _e("a", "20250110", 12000, "옛구독"),
        _e("b", "20250210", 12000, "옛구독"),
        _e("c", "20250310", 12000, "옛구독"),
    ]
    series = find_recurring_series(entries, as_of="20260601")
    assert len(series) == 1
    assert series[0].discontinued is True
    assert series[0].missing == ()
    # omission 결과에는 안 나온다 (해지 구독 닦달 방지).
    assert detect_recurring_omissions(entries, as_of="20260601") == []


# ---- 그룹핑 / 금액 변동 ------------------------------------------------


def test_grouping_ignores_amount_variation():
    # 공과금처럼 금액이 매달 달라도 같은 시리즈로 묶이고 중앙값을 대표로.
    entries = [
        _e("a", "20260105", 30000, "전기요금"),
        _e("b", "20260205", 42000, "전기요금"),
        _e("c", "20260405", 35000, "전기요금"),  # 3월 빠짐.
        _e("d", "20260505", 33000, "전기요금"),
    ]
    flagged = detect_recurring_omissions(entries, as_of="20260520")
    assert len(flagged) == 1
    s = flagged[0]
    assert s.typical_money == 34000  # median(30000,42000,35000,33000)
    assert s.gap_count == 1


def test_distinct_items_not_merged():
    a = _monthly("넷플릭스", ["01", "02", "03", "04"])
    b = _monthly("유튜브프리미엄", ["01", "02", "03", "04"])
    series = find_recurring_series(a + b, as_of="20260420")
    items = sorted(s.item_norm for s in series)
    assert items == ["넷플릭스", "유튜브프리미엄"]


def test_near_duplicate_same_day_collapsed_to_one_event():
    # 같은 회차에 이중 등록(2일 차) — 한 이벤트로 병합돼 주기 왜곡 없음.
    entries = _monthly("정기후원", ["01", "02", "03", "04"], day="10")
    entries.append(_e("dup", "20260212", 9900, "정기후원"))  # 02-10 회차 중복.
    series = find_recurring_series(entries, as_of="20260420")
    assert len(series) == 1
    assert series[0].occurrences == 4  # 5건이지만 4회차.


def test_omission_sort_overdue_first():
    # 연체 있는 시리즈가 gap 만 있는 시리즈보다 먼저.
    gap_only = _monthly("월세", ["01", "02", "04", "05"], day="15",
                        money=600000, prefix="r")
    overdue = _monthly("헬스장", ["01", "02", "03"], day="10",
                       money=50000, prefix="h")
    flagged = detect_recurring_omissions(gap_only + overdue, as_of="20260425")
    assert flagged[0].has_overdue


def test_cadence_labels_present():
    for key in ("weekly", "biweekly", "monthly", "quarterly", "yearly"):
        assert key in CADENCE_LABELS_KO
