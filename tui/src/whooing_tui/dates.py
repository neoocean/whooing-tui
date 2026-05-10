"""KST 날짜 유틸.

후잉의 모든 날짜는 KST 자정 기준 YYYYMMDD 문자열. 본 도구는 호스트의
시간대와 무관하게 항상 `Asia/Seoul` 강제.

본래 whooing-mcp-server-wrapper (archived 2026-05-10) 의 동일 모듈에서
인용 — 두 도구가 같은 날짜 규칙을 공유하도록 한 의도적 코드 중복. wrapper
종료 후에도 단독으로 유지.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def today_yyyymmdd() -> str:
    return now_kst().strftime("%Y%m%d")


def days_ago_yyyymmdd(days: int) -> str:
    """`days` 일 전의 KST 날짜를 YYYYMMDD 로 반환. days=0 이면 오늘."""
    if days < 0:
        raise ValueError(f"days must be >= 0, got {days}")
    return (now_kst() - timedelta(days=days)).strftime("%Y%m%d")


def parse_yyyymmdd(s: str) -> str:
    """문자열이 유효한 YYYYMMDD 형식인지 검증하고 그대로 반환.

    잘못된 입력에는 ValueError. 호출자가 ToolError 로 변환한다.
    """
    if not isinstance(s, str) or len(s) != 8 or not s.isdigit():
        raise ValueError(f"Expected YYYYMMDD (8자리 숫자), got: {s!r}")
    # 실제 달력상 유효한 날짜인지 (예: 20260230 차단)
    datetime.strptime(s, "%Y%m%d")
    return s


def parse_yyyymm(s: str) -> str:
    """월 단위 조회용 YYYYMM 검증. 잘못된 입력엔 ValueError."""
    if not isinstance(s, str) or len(s) != 6 or not s.isdigit():
        raise ValueError(f"Expected YYYYMM (6자리 숫자), got: {s!r}")
    month = int(s[4:])
    if not (1 <= month <= 12):
        raise ValueError(f"월(MM) 은 01~12: got {s!r}")
    return s


def date_diff_days(a: str, b: str) -> int:
    """|date(a) - date(b)| 을 일 단위 정수로 반환. 입력은 YYYYMMDD."""
    da = datetime.strptime(parse_yyyymmdd(a), "%Y%m%d")
    db = datetime.strptime(parse_yyyymmdd(b), "%Y%m%d")
    return abs((da - db).days)


def split_yearly_ranges(start: str, end: str) -> list[tuple[str, str]]:
    """후잉 entries.json 의 1년 제약을 위해 365일 단위로 분할.

    반환은 [(start1, end1), (start2, end2), ...] inclusive 범위. 분할이
    필요 없으면 [(start, end)] 1개. start <= end 가정 (검증은 호출자).
    """
    parse_yyyymmdd(start)
    parse_yyyymmdd(end)
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    if (e - s).days <= 365:
        return [(start, end)]
    out: list[tuple[str, str]] = []
    cur = s
    while cur <= e:
        chunk_end = min(cur + timedelta(days=365), e)
        out.append((cur.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        cur = chunk_end + timedelta(days=1)
    return out
