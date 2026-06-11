"""반복 거래 누락 감지 — 주기적 거래의 '빠진 회차' 휴리스틱 (pure).

사용자 요청 (고도화):
> 이전에 LLM 으로 하던 '반복거래누락탐지' 를 휴리스틱 기반(비 LLM)으로
> 고도화해 기능으로 추가. 매월 나가는 구독료·월세·급여처럼 *규칙적으로
> 반복되는* 거래가 어느 주기에 빠졌는지 찾아준다.

`dupes.py` 가 *너무 많이* 입력된 거래(중복)를 찾는다면, 본 모듈은 그 반대 —
*있어야 하는데 빠진* 거래를 찾는다. 둘 다 거래내역의 정합성을 지키는 도구.

본 모듈은 pure function — sqlite / 후잉 / Textual 의존 없음. 입력은 entry
dict list, 출력은 `RecurringSeries` (시리즈 + 누락 회차) list.

알고리즘 개요:
  1. (왼쪽계정, 오른쪽계정, 정규화 item) 으로 거래를 *시리즈* 로 묶는다.
     금액은 변동(공과금 등)할 수 있어 key 에서 제외하되 대표 금액은 표시.
  2. 시리즈 안에서 날짜순 정렬 → 같은 회차로 보이는 근접 거래(±3일)는 한
     이벤트로 병합 → 연속 이벤트 사이 간격(일수)을 구한다.
  3. 간격의 중앙값으로 주기(매주/격주/매월/분기/매년)를 분류. 분류 안 되면
     '규칙적'이 아니므로 skip.
  4. 규칙성(간격이 주기의 정수배 ± 허용오차에 드는 비율)이 임계 이상이어야
     시리즈로 인정 — 우연히 비슷한 거래의 false positive 차단.
  5. 첫 이벤트에서 주기마다 *기대 날짜* 를 투영(달 기반은 달력 연산)하고,
     기대 날짜 ± 허용오차 안에 실제 거래가 없으면 *누락* 으로 표시.
       - gap      — 실제 거래 *사이* 에 빠진 회차 (강한 신호: 재개됐으므로
                    그 사이 회차는 진짜 깜빡 누락).
       - overdue  — 마지막 거래 *이후* 기대됐으나 안 들어온 회차 (현재 연체).
  6. 연체가 너무 오래(3주기 초과) 쌓이면 '구독 종료'로 보고 누락 보고 안 함
     (해지한 구독을 매달 누락이라 알리는 false positive 방지).

이렇게 하면:
  - 매달 내던 월세에서 3월만 빠짐 → gap 누락으로 잡음.
  - 지난달 구독료를 깜빡함 → overdue 1건으로 잡음.
  - 반년 전 해지한 넷플릭스 → 보고 안 함 (discontinued).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Any, Iterable

from whooing_core.dupes import normalize_text

# ---- 튜닝 상수 ---------------------------------------------------------

# 시리즈로 인정할 최소 이벤트(회차) 수. 2건으로는 주기를 추정할 수 없다.
MIN_OCCURRENCES = 3

# 이만큼 가까운 날짜의 거래는 같은 회차로 병합 (이중 승인 / 정정 흡수).
SAME_EVENT_DAYS = 3

# 간격이 주기의 정수배 ± 허용오차에 드는 이벤트 비율이 이 이상이어야 규칙적.
MIN_REGULARITY = 0.6

# 마지막 거래 이후 기대 날짜를 '연체'로 부르기 전 허용오차에 더해줄 유예.
GRACE_DAYS = 5

# 연체로 보고할 최대 회차 수 (연체가 더 많아도 N건까지만 표시).
MAX_TRAILING = 2

# 연체가 이 주기 수를 초과하면 시리즈가 종료됐다고 보고 연체 보고 안 함.
DISCONTINUED_PERIODS = 3


@dataclass(frozen=True)
class _Cadence:
    """주기 1종 — 분류용 간격 band + 투영용 step + 매칭 허용오차."""

    name: str          # 'weekly' | 'biweekly' | 'monthly' | 'quarterly' | 'yearly'
    step_months: int   # 달 기반 주기면 step 달 수, 주 기반이면 0.
    base_days: int     # 정규 주기 일수 (분류 / 규칙성 계산용).
    lo: int            # 중앙값 간격이 이 band [lo, hi] 안이면 이 주기.
    hi: int
    tol: int           # 기대 ↔ 실제 매칭 허용오차 (일).


# band 는 서로 겹치지 않게 (사이 구간은 '주기 불명'으로 skip — bimonthly 등).
_CADENCES: tuple[_Cadence, ...] = (
    _Cadence("weekly", step_months=0, base_days=7, lo=5, hi=9, tol=2),
    _Cadence("biweekly", step_months=0, base_days=14, lo=11, hi=18, tol=3),
    _Cadence("monthly", step_months=1, base_days=30, lo=24, hi=37, tol=6),
    _Cadence("quarterly", step_months=3, base_days=91, lo=80, hi=100, tol=10),
    _Cadence("yearly", step_months=12, base_days=365, lo=330, hi=400, tol=20),
)

CADENCE_LABELS_KO: dict[str, str] = {
    "weekly": "매주",
    "biweekly": "격주",
    "monthly": "매월",
    "quarterly": "분기",
    "yearly": "매년",
}


@dataclass(frozen=True)
class MissingOccurrence:
    """시리즈에서 빠진 회차 1건.

    expected_date: 기대됐던 날짜 (YYYYMMDD).
    kind: 'gap' (실제 거래 사이의 누락) | 'overdue' (마지막 이후 연체).
    """

    expected_date: str
    kind: str


@dataclass(frozen=True)
class RecurringSeries:
    """반복 거래 시리즈 1개 + 누락 회차.

    l_account_id / r_account_id / item: 시리즈를 식별하는 서명.
    cadence: 분류된 주기 이름. period_days: 그 정규 일수.
    occurrences: 병합 후 이벤트(회차) 수. first_date / last_date: 처음/마지막.
    typical_money: 회차 금액의 중앙값(절대값) — 표시 / 신규 입력 제안용.
    sample: 가장 최근 거래 dict (계정/금액/메모 표시에 사용).
    entry_ids: 시리즈에 속한 모든 후잉 entry_id.
    missing: 누락 회차 list (gap 먼저, 날짜순).
    regularity: 0..1 — 간격이 주기에 들어맞은 비율 (신뢰도).
    discontinued: True 면 종료된 시리즈로 판단(연체 미보고).
    """

    l_account_id: str
    r_account_id: str
    item: str
    item_norm: str
    cadence: str
    period_days: int
    occurrences: int
    first_date: str
    last_date: str
    typical_money: int | None
    sample: dict[str, Any]
    entry_ids: tuple[str, ...]
    missing: tuple[MissingOccurrence, ...]
    regularity: float
    discontinued: bool = False

    @property
    def has_overdue(self) -> bool:
        return any(m.kind == "overdue" for m in self.missing)

    @property
    def gap_count(self) -> int:
        return sum(1 for m in self.missing if m.kind == "gap")

    @property
    def overdue_count(self) -> int:
        return sum(1 for m in self.missing if m.kind == "overdue")


# ---- 날짜 유틸 (pure) --------------------------------------------------


def _parse(v: Any) -> date | None:
    """YYYYMMDD (또는 YYYYMMDD.NNNN) → date. 잘못된 입력은 None."""
    if v is None:
        return None
    s = str(v).strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def _add_months(d: date, months: int, anchor_day: int) -> date:
    """`d` 기준 `months` 달 뒤 — 일(day)은 anchor_day 로 고정(월말 clamp)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    mm = m % 12 + 1
    last = calendar.monthrange(y, mm)[1]
    return date(y, mm, min(anchor_day, last))


def _abs_money(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return abs(int(v))
    except (TypeError, ValueError):
        try:
            return abs(int(float(v)))
        except (TypeError, ValueError):
            return None


# ---- 핵심: 시리즈 추출 + 누락 투영 -------------------------------------


@dataclass
class _Event:
    """병합된 회차 1개 — 대표 날짜 + 그 회차에 묶인 거래들."""

    when: date
    entries: list[dict[str, Any]] = field(default_factory=list)


def _collapse_events(members: list[dict[str, Any]]) -> list[_Event]:
    """거래들을 날짜순 정렬 후 근접(±SAME_EVENT_DAYS) 거래를 한 회차로 병합."""
    dated = [
        (d, e) for e in members if (d := _parse(e.get("entry_date"))) is not None
    ]
    dated.sort(key=lambda pair: pair[0])
    events: list[_Event] = []
    for d, e in dated:
        if events and (d - events[-1].when).days <= SAME_EVENT_DAYS:
            events[-1].entries.append(e)
        else:
            events.append(_Event(when=d, entries=[e]))
    return events


def _classify(median_gap: float) -> _Cadence | None:
    for c in _CADENCES:
        if c.lo <= median_gap <= c.hi:
            return c
    return None


def _regularity(gaps: list[int], base_days: int, tol: int) -> float:
    """간격들이 주기의 정수배 ± tol 에 드는 비율 (0..1)."""
    if not gaps:
        return 0.0
    good = 0
    for g in gaps:
        k = max(1, round(g / base_days))
        if abs(g - k * base_days) <= tol:
            good += 1
    return good / len(gaps)


def _project_expected(
    events: list[_Event], cad: _Cadence, as_of: date,
) -> list[date]:
    """첫 이벤트에서 주기마다 기대 날짜를 as_of 직후까지 투영 (첫 회차 제외)."""
    first = events[0].when
    anchor_day = first.day
    out: list[date] = []
    horizon = as_of + timedelta(days=cad.base_days)
    k = 1
    while k <= 2000:  # safety — 2000 주기면 어떤 현실 범위도 초과.
        if cad.step_months:
            e = _add_months(first, k * cad.step_months, anchor_day)
        else:
            e = first + timedelta(days=k * cad.base_days)
        if e > horizon:
            break
        out.append(e)
        k += 1
    return out


def _find_missing(
    events: list[_Event], cad: _Cadence, as_of: date,
) -> tuple[list[MissingOccurrence], bool]:
    """기대 날짜를 실제 이벤트와 매칭해 누락(gap/overdue) + discontinued 판정."""
    last = events[-1].when
    expected = _project_expected(events, cad, as_of)
    event_dates = [ev.when for ev in events]
    used = [False] * len(event_dates)

    gaps: list[date] = []
    overdue: list[date] = []
    for e in expected:
        matched = False
        for i, ev_when in enumerate(event_dates):
            if used[i]:
                continue
            if abs((ev_when - e).days) <= cad.tol:
                used[i] = True
                matched = True
                break
        if matched:
            continue
        if e <= last:
            gaps.append(e)
        elif (as_of - e).days > cad.tol + GRACE_DAYS:
            overdue.append(e)

    discontinued = len(overdue) > DISCONTINUED_PERIODS
    if discontinued:
        overdue = []  # 종료된 시리즈는 연체로 닦달하지 않음.
    else:
        overdue = overdue[:MAX_TRAILING]

    missing = [MissingOccurrence(_fmt(d), "gap") for d in gaps]
    missing += [MissingOccurrence(_fmt(d), "overdue") for d in overdue]
    missing.sort(key=lambda m: m.expected_date)
    return missing, discontinued


def find_recurring_series(
    entries: Iterable[dict[str, Any]],
    *,
    as_of: str | None = None,
    min_occurrences: int = MIN_OCCURRENCES,
) -> list[RecurringSeries]:
    """전체 거래에서 규칙적 반복 시리즈를 추출 (누락 유무 무관).

    Args:
        entries: 후잉 entries-list dict iterable. l_account_id / r_account_id /
                 item / entry_date / money / entry_id 사용.
        as_of: 연체 판정 기준일 (YYYYMMDD). None 이면 데이터의 가장 최근
               거래일을 기준으로 — 단, 그 경우 마지막 이후 연체는 잡히지
               않으므로 TUI 는 항상 오늘 날짜를 넘겨야 한다.
        min_occurrences: 시리즈로 인정할 최소 회차 수.

    Returns:
        RecurringSeries list (정렬 안 됨 — 호출자가 목적에 맞게 정렬).
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for e in entries:
        item_norm = normalize_text(e.get("item"))
        if not item_norm:
            continue
        l = str(e.get("l_account_id") or "")
        r = str(e.get("r_account_id") or "")
        if not l or not r:
            continue
        groups.setdefault((l, r, item_norm), []).append(e)

    # as_of 결정 — 명시 없으면 데이터 최신일.
    as_of_date = _parse(as_of) if as_of else None
    if as_of_date is None:
        all_dates = [
            d for e in entries if (d := _parse(e.get("entry_date"))) is not None
        ]
        as_of_date = max(all_dates) if all_dates else date.today()

    out: list[RecurringSeries] = []
    for (l, r, item_norm), members in groups.items():
        events = _collapse_events(members)
        if len(events) < min_occurrences:
            continue
        gaps_days = [
            (events[i + 1].when - events[i].when).days
            for i in range(len(events) - 1)
        ]
        if not gaps_days:
            continue
        med = median(gaps_days)
        cad = _classify(med)
        if cad is None:
            continue
        reg = _regularity(gaps_days, cad.base_days, cad.tol)
        if reg < MIN_REGULARITY:
            continue

        missing, discontinued = _find_missing(events, cad, as_of_date)

        all_entries = [e for ev in events for e in ev.entries]
        moneys = [
            m for e in all_entries if (m := _abs_money(e.get("money"))) is not None
        ]
        typical = int(median(moneys)) if moneys else None
        sample = events[-1].entries[-1]
        # 대표 item — 가장 최근 거래의 raw item (정규화 전 표기 보존).
        item_raw = str(sample.get("item") or "")
        entry_ids = tuple(
            str(e.get("entry_id") or "") for e in all_entries if e.get("entry_id")
        )

        out.append(RecurringSeries(
            l_account_id=l,
            r_account_id=r,
            item=item_raw,
            item_norm=item_norm,
            cadence=cad.name,
            period_days=cad.base_days,
            occurrences=len(events),
            first_date=_fmt(events[0].when),
            last_date=_fmt(events[-1].when),
            typical_money=typical,
            sample=sample,
            entry_ids=entry_ids,
            missing=tuple(missing),
            regularity=reg,
            discontinued=discontinued,
        ))
    return out


def detect_recurring_omissions(
    entries: Iterable[dict[str, Any]],
    *,
    as_of: str | None = None,
    min_occurrences: int = MIN_OCCURRENCES,
) -> list[RecurringSeries]:
    """반복 시리즈 중 *누락이 있는* 것만 추려 심각도순으로 반환.

    정렬: 연체 있는 것 먼저 → 누락 건수 많은 순 → 회차 많은 순(신뢰도) →
    금액 큰 순. 누락 없는 시리즈는 제외 (find_recurring_series 로 전체 조회).
    """
    items = list(entries)
    series = find_recurring_series(
        items, as_of=as_of, min_occurrences=min_occurrences,
    )
    flagged = [s for s in series if s.missing]
    flagged.sort(key=lambda s: (
        not s.has_overdue,           # 연체 있는 것 먼저.
        -len(s.missing),
        -s.occurrences,
        -(s.typical_money or 0),
    ))
    return flagged


__all__ = [
    "MissingOccurrence",
    "RecurringSeries",
    "CADENCE_LABELS_KO",
    "MIN_OCCURRENCES",
    "find_recurring_series",
    "detect_recurring_omissions",
]
