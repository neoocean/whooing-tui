"""EntriesScreen 의 컴팩트 모드 / 약어 logic — pure helpers 분리.

CL #51158+ (review C4): 종전엔 entries.py 가 2431 줄 + 80+ 메서드 = God
Object. 본 모듈이 가장 testable + side-effect-free 한 부분 (약어 / 임계값
/ column visibility) 을 module-level helpers 로 추출.

EntriesScreen 은 본 helpers 를 import 해 그대로 위임 — 후방 호환 (classmethod
/ 인스턴스 메서드 모두 유지).
"""

from __future__ import annotations

# ---- 컴팩트 단계 임계값 (사용자 요청 CL #51125) ----------------------------

# 단계별 폭 임계값 (내림차순):
#   level 0 (>=80): 정상 (6 컬럼).
#   level 1 (<80) : memo 숨김.
#   level 2 (<60) : + left/right 헤더 'L'/'R' 약어, 셀은 한글 2글자.
#   level 3 (<45) : + right 컬럼 숨김.
#   level 4 (<35) : + left 컬럼도 숨김.
COMPACT_THRESHOLDS: tuple[int, ...] = (80, 60, 45, 35)


def compute_compact_level(width: int) -> int:
    """현재 터미널 너비로 컴팩트 단계 (0~4) 계산.

    `COMPACT_THRESHOLDS` 가 내림차순이라 width 가 한 threshold 이상이면
    이후 도 모두 이상 — 조기 break.
    """
    level = 0
    for threshold in COMPACT_THRESHOLDS:
        if width < threshold:
            level += 1
        else:
            break
    return level


# ---- 한국식 줄임말 (사용자 요청 CL #51127 / #51130) ------------------------

# 괄호류 — 약어 시 사전 제거. 한글 인용부호 ( 「」 『』 ) 까지.
ABBREV_BRACKETS: str = "()[]{}「」『』"

# 회사 식별자 prefix — strip 후 본 이름만 (선두 매칭 1회).
# 더 긴 prefix 부터 시도 (`주식회사` 가 `(주)` 보다 먼저).
ABBREV_COMPANY_PREFIXES: tuple[str, ...] = (
    "주식회사 ", "주식회사",
    "유한회사 ", "유한회사",
    "재단법인 ", "재단법인",
    "사단법인 ", "사단법인",
    "(주)", "(유)", "(재)", "(사)",
)

# 회사 식별자 suffix — strip 후 길이 재판정 (CL #51130).
# 보수적으로 *대문자 회사 명사* 만. 더 긴 suffix 먼저 ("인터내셔널" > "내셔널").
ABBREV_COMPANY_SUFFIXES: tuple[str, ...] = (
    "엔터프라이즈", "인터내셔널",
    "코퍼레이션", "이노베이션",
    "홀딩스", "그룹", "글로벌", "코리아",
)

# 한글 음절 범위 (Hangul Syllables U+AC00~U+D7A3) — 첫 글자가 한글이면
# 한국식 줄임말 규칙 적용, 그 외 (영문/숫자) 면 단순 [:N].
HANGUL_FIRST = 0xAC00
HANGUL_LAST = 0xD7A3
ABBREV_CHARS: int = 2  # 한글 기준 앞 N 글자.


def is_hangul(ch: str) -> bool:
    """단일 글자가 한글 음절 (Hangul Syllables block) 인지."""
    if not ch:
        return False
    c = ord(ch[0])
    return HANGUL_FIRST <= c <= HANGUL_LAST


def abbreviate_account_name(name: str) -> str:
    """계정명을 좁은 컬럼용 약어. CL #51127/#51130 의 한국식 규칙.

    절차:
      1. 회사 prefix strip (한 번만, 더 긴 것부터).
      2. 잔여 괄호류 strip.
      3. 회사 suffix strip (잔여 ≥2 자일 때만 — 안전망).
      4. 길이 + 한글 첫글자 분기:
         - 빈 문자열 → "".
         - 첫 글자 한글 + 길이 4 → 1번째 + 3번째 (스벅 / 맥날 / 삼전).
         - 첫 글자 한글 + 길이 3 → 앞 2글자.
         - 첫 글자 한글 + 길이 2 이하 → 그대로.
         - 첫 글자 한글 + 길이 5 이상 → 앞 2글자 (보수 fallback).
         - 첫 글자 비-한글 → 앞 2글자.

    예:
      "(주)스타벅스코리아"   → "스벅"
      "스타벅스"             → "스벅"  (4자, 1+3)
      "맥도날드"             → "맥날"
      "주식회사 카카오"      → "카카"
      "교통비"               → "교통"
      "식비"                 → "식비"
      "현대자동차"           → "현대"
      "Starbucks"            → "St"
    """
    if not name:
        return ""
    cleaned = name.strip()
    # 1. 회사 prefix strip.
    for prefix in ABBREV_COMPANY_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].lstrip()
            break
    # 2. 잔여 괄호류 제거.
    cleaned = "".join(ch for ch in cleaned if ch not in ABBREV_BRACKETS)
    cleaned = cleaned.strip()
    # 3. 회사 suffix strip — 잔여 ≥2 자일 때만 (안전망).
    for suffix in ABBREV_COMPANY_SUFFIXES:
        if cleaned.endswith(suffix):
            stripped = cleaned[: -len(suffix)].rstrip()
            if len(stripped) >= 2:
                cleaned = stripped
                break
    if not cleaned:
        return ""
    n = len(cleaned)
    # 4. 분기 — 한글 우선.
    if is_hangul(cleaned[0]):
        if n <= 2:
            return cleaned
        if n == 3:
            return cleaned[:2]
        if n == 4:
            return cleaned[0] + cleaned[2]
        return cleaned[:2]
    return cleaned[:ABBREV_CHARS]


# ---- 컬럼 visibility (네비 skip 정책) — CL #51125 -------------------------

# 컬럼 인덱스 정의 (entries.py 의 _COLUMN_NAMES 와 1:1):
#   0 date / 1 money / 2 left / 3 right / 4 item / 5 memo


def hidden_columns_for_level(level: int) -> set[int]:
    """컴팩트 level 별 hidden 컬럼 인덱스 set.

      level 0: 모두 visible.
      level 1: memo (5).
      level 2: memo (5) — left/right 는 약어 visible.
      level 3: memo + right (3, 5).
      level 4: memo + right + left (2, 3, 5).
    """
    if level >= 4:
        return {2, 3, 5}
    if level >= 3:
        return {3, 5}
    if level >= 1:
        return {5}
    return set()


def column_is_visible(col_index: int, level: int) -> bool:
    """col_index 가 현재 level 에서 visible 한지 — 네비 skip 용."""
    return col_index not in hidden_columns_for_level(level)
