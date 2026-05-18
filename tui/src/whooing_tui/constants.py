"""모든 매직 상수를 모은 단일 모듈 (CL #52834+).

CL #52833 의 유지보수 감사 결과: 같은 상수가 여러 파일에 흩어져 있어
변경 시 누락 위험. 본 모듈로 일원화 — caller 가 `from whooing_tui import
constants as C` 후 `C.WHOOING_SERVER_PAGE_CAP` 처럼 사용.

원칙:
- *명명된 상수만* — 임시 magic number 는 caller 안의 지역 변수로.
- 변경 빈도가 낮고 의미가 안정적인 값만 — config 가 더 적절하면 거기.
- 한글 codepoint 같은 *수학적 상수* 도 포함.
"""

from __future__ import annotations

# ---- 후잉 API ----------------------------------------------------------

# 후잉 server-side `entries-list` 의 한 페이지 hard cap.
# DESIGN.md §4.3 + MEMORY.md §7 의 일자별 cap 경고 정책 근간.
WHOOING_SERVER_PAGE_CAP: int = 100


# ---- 거래 윈도우 ------------------------------------------------------

# 최근 N일 윈도우의 기본값. config.entries.default_window_days 와 일치
# (config 가 override 안 하면 본 값 사용).
DEFAULT_WINDOW_DAYS: int = 30

# +/- 키 한 번에 윈도우가 변하는 일수.
WINDOW_STEP_DAYS: int = 7

# 윈도우 최대 크기 (5 년). 너무 큰 윈도우는 후잉 cap 으로 무의미.
MAX_WINDOW_DAYS: int = 365 * 5

# 윈도우 최소 크기.
MIN_WINDOW_DAYS: int = 1


# ---- 한글 (Hangul Syllables 블록) -------------------------------------

# Hangul Syllables 첫 음절 ('가') / 마지막 음절 ('힣') codepoint.
# 약어 / IME / 한글 첫 글자 검사 등에서 사용.
HANGUL_SYLLABLE_FIRST: int = 0xAC00
HANGUL_SYLLABLE_LAST: int = 0xD7A3


# ---- 한국식 약어 -----------------------------------------------------

# 한글 회사명 등 *첫 글자가 한글일 때* 표시 약어 글자 수 (예: "스타벅스
# 강남점" → "스벅"). 영문/숫자 시작이면 단순 슬라이스 [:2].
ABBREV_KOREAN_CHARS: int = 2


# ---- 컴팩트 단계 임계값 ------------------------------------------------

# 터미널 폭에 따른 컬럼 숨김 임계값. 각 임계값보다 좁으면 한 단계 더
# 컴팩트. [80, 60, 45, 35]:
#   >= 80 → 정상 (6 컬럼).
#   < 80  → memo 숨김.
#   < 60  → left/right 컬럼명 약어 + 셀 한글 2자.
#   < 45  → right 숨김.
#   < 35  → left 도 숨김.
COMPACT_THRESHOLDS: tuple[int, ...] = (80, 60, 45, 35)


# ---- P4 동기화 -------------------------------------------------------

# 종료 시 wait_for_pending 의 thread 별 join 타임아웃. _do_submit
# 내부의 `p4 submit -d ...` 타임아웃 (30s) 과 동일.
P4_PENDING_JOIN_TIMEOUT_SEC: float = 30.0

# `p4` subprocess 의 기본 타임아웃 (info / where / sync -n 등 가벼운 호출).
P4_DEFAULT_TIMEOUT_SEC: int = 30

# 가벼운 preview 호출 (sync -n / where) 의 타임아웃 — 더 짧게.
P4_LIGHT_PREVIEW_TIMEOUT_SEC: int = 10


# ---- UI 갱신 주기 ----------------------------------------------------

# _ShutdownModal / _StartupCheckScreen 의 라이브 정보 refresh 주기.
LIVE_REFRESH_INTERVAL_SEC: float = 0.25


__all__ = [
    "WHOOING_SERVER_PAGE_CAP",
    "DEFAULT_WINDOW_DAYS", "WINDOW_STEP_DAYS",
    "MAX_WINDOW_DAYS", "MIN_WINDOW_DAYS",
    "HANGUL_SYLLABLE_FIRST", "HANGUL_SYLLABLE_LAST",
    "ABBREV_KOREAN_CHARS",
    "COMPACT_THRESHOLDS",
    "P4_PENDING_JOIN_TIMEOUT_SEC",
    "P4_DEFAULT_TIMEOUT_SEC",
    "P4_LIGHT_PREVIEW_TIMEOUT_SEC",
    "LIVE_REFRESH_INTERVAL_SEC",
]
