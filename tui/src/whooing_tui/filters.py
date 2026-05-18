"""클라이언트-사이드 거래내역 필터 — `EntriesScreen` 의 Enter 컬럼 액션
에서 사용. 후잉 추가 호출 없이 화면의 entries list 를 부분집합으로 좁힌다.

지원하는 필터 (CL #51053+):

  - **date**     : 같은 entry_date (sub-index 무시).
  - **left**     : 같은 l_account_id.
  - **right**    : 같은 r_account_id.
  - **item**     : 괄호 바깥 키워드 중 하나라도 같으면 매칭.
  - **memo**     : (CL #52757+) target memo 의 키워드 (≥2글자) 중 하나라도
                   다른 entry 의 memo 에 substring 포함되면 매칭. 사용자
                   요청: memo 는 자유 입력이라 정확 일치는 거의 없으므로
                   "비슷한" (= 키워드 substring) 으로 매칭.

money 컬럼은 필터 대상 아님 — Enter 시 edit_entry 로.

`_apply_filter` 가 (target, column, all_entries) 를 받아 부분집합 list 를
반환 — pure 함수, side-effect 없음. 테스트 친화.
"""

from __future__ import annotations

import re
from typing import Any


def date_head(value: Any) -> str:
    """후잉 응답의 entry_date 가 `"20260510.0001"` 처럼 sub-index 가 붙어
    있을 수 있으므로 `.` 앞 8자리만 비교 키로 사용. 같은 정책을 표 표시
    (`screens/entries.py::_fmt_date`) 와 공유.
    """
    if value is None:
        return ""
    return str(value).split(".", 1)[0]


def outside_paren_keywords(item: Any) -> set[str]:
    """item 의 괄호 바깥 부분을 공백/콤마로 split 한 키워드 set.

    예:
      "스타벅스(커피)"          → {"스타벅스"}
      "외식(저녁, 불고기)"       → {"외식"}
      "교통(버스) 주차"          → {"교통", "주차"}
      "월급"                    → {"월급"}
      None / ""                 → set()

    빈 set 인 경우 (괄호 바깥에 키워드 없음) 매칭 비교 없이 0건 반환 —
    호출자 책임.
    """
    if not item:
        return set()
    s = str(item)
    # 괄호와 그 안의 모든 내용 제거. 중첩 괄호는 후잉 데이터에 사실상
    # 없으므로 단순 non-greedy 로 처리.
    outside = re.sub(r"\([^)]*\)", "", s)
    parts = re.split(r"[,\s]+", outside)
    return {p.strip() for p in parts if p.strip()}


def memo_keywords(memo: Any) -> set[str]:
    """memo 의 의미 있는 키워드 set — 공백/쉼표/괄호 분리 + 2글자 이상.

    예:
      "우유, 수박 (쿠팡)"   → {"우유", "수박", "쿠팡"}
      "택시"                → {"택시"}
      "abc"                 → {"abc"}
      "a"                   → set()  (1글자 무시 — false positive 방지)
      None / ""             → set()

    CL #52757+. 한 글자 토큰은 너무 흔해 다른 entry 와 우연 매칭 (예: "비")
    가능성 — 의도적 제외.
    """
    if not memo:
        return set()
    s = str(memo)
    # 공백 / 쉼표 / 괄호 [] / 작은 따옴표 모두 separator.
    parts = re.split(r"[\s,()\[\]'\"]+", s)
    return {p.strip() for p in parts if len(p.strip()) >= 2}


# 컬럼 이름 ↔ 필터 가능 여부.
# CL #52757+: memo 추가 — substring 매칭 (memo_keywords 기반).
FILTERABLE_COLUMNS: tuple[str, ...] = ("date", "left", "right", "item", "memo")


def filter_entries(
    entries: list[dict[str, Any]],
    column: str,
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    """주어진 컬럼 기준으로 `entries` 의 부분집합을 반환.

    `column` 이 `FILTERABLE_COLUMNS` 에 없으면 빈 list — 호출자가 `target`
    이 매칭에 충분한 정보를 갖지 않을 때 (예: item 의 괄호 바깥이 비어
    있을 때) 도 빈 list. UI 는 그 결과를 그대로 표시 + status 에 0건
    안내한다.
    """
    if column == "date":
        head = date_head(target.get("entry_date"))
        if not head:
            return []
        return [e for e in entries if date_head(e.get("entry_date")) == head]

    if column == "left":
        target_id = target.get("l_account_id") or ""
        if not target_id:
            return []
        return [e for e in entries if e.get("l_account_id") == target_id]

    if column == "right":
        target_id = target.get("r_account_id") or ""
        if not target_id:
            return []
        return [e for e in entries if e.get("r_account_id") == target_id]

    if column == "item":
        target_keys = outside_paren_keywords(target.get("item"))
        if not target_keys:
            return []
        return [
            e for e in entries
            if outside_paren_keywords(e.get("item")) & target_keys
        ]

    if column == "memo":
        # CL #52757+: memo 는 자유 입력이라 정확 일치 거의 없음 — substring.
        # target 의 keyword 중 하나라도 다른 memo 에 substring 으로 들어있으면
        # 매칭. 토큰 비교는 outside_paren_keywords 와 비슷하지만 길이 cap 만
        # 있고 괄호 strip 은 안 함 (memo 안 괄호도 유의미).
        target_keys = memo_keywords(target.get("memo"))
        if not target_keys:
            return []
        return [
            e for e in entries
            if any(k in (e.get("memo") or "") for k in target_keys)
        ]

    return []
