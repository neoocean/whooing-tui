"""core/hangul.py — 자모 → 음절 조합 단위 테스트.

iOS Blink 등 일부 terminal 이 한국어 IME keystroke 를 자모 단위로 보낼
때, 본 모듈이 sequence 를 음절로 합성. 단위 — pure 함수.
"""

from __future__ import annotations

import pytest

from whooing_core.hangul import compose_hangul


# ---- 기본 음절 합성 (Compat Jamo) ----------------------------------


@pytest.mark.parametrize("src, expected", [
    ("ㅎㅏㄴ", "한"),
    ("ㅎㅏ", "하"),
    ("ㄱㅏ", "가"),
    ("ㅂㅓㄱ", "벅"),
    ("ㅅㅡ", "스"),
    ("ㅁㅔㅁㅗ", "메모"),
    ("ㅎㅏㄴㄱㅜㄱ", "한국"),
    ("ㅇㅏㄴㄴㅕㅇ", "안녕"),
    ("ㅇㅏㄴㄴㅕㅇㅎㅏㅅㅔㅇㅛ", "안녕하세요"),
    ("ㅅㅡㅌㅏㅂㅓㄱㅅㅡ", "스타벅스"),
])
def test_compose_compat_jamo_to_syllables(src, expected):
    """Hangul Compatibility Jamo (U+3130~U+318F) sequence → 음절."""
    assert compose_hangul(src) == expected


# ---- 단독 자모 (자모 만 입력하고 멈춤) ------------------------------


def test_compose_single_jamo_pass_through():
    """자모 1개만 — 그대로 표시 (사용자가 다음 글자 칠 중)."""
    assert compose_hangul("ㅎ") == "ㅎ"
    assert compose_hangul("ㄱ") == "ㄱ"


# ---- ASCII / 혼합 ---------------------------------------------------


def test_ascii_passthrough():
    assert compose_hangul("abc") == "abc"
    assert compose_hangul("hello world") == "hello world"
    assert compose_hangul("") == ""


def test_mixed_ascii_and_hangul():
    """ASCII 와 자모 혼합 — 자모만 합성."""
    assert compose_hangul("a한b") == "a한b"
    assert compose_hangul("ㅎㅏabㄱㅜㄱ") == "하ab국"
    assert compose_hangul("hello 안녕 ") == "hello 안녕 "


# ---- 이미 조합된 음절 처리 -----------------------------------------


def test_already_composed_syllable_passthrough():
    """완성된 음절은 그대로 — 변환 없음."""
    assert compose_hangul("한국") == "한국"
    assert compose_hangul("스타벅스") == "스타벅스"


def test_syllable_plus_jamo_recomposes():
    """음절 뒤에 자모 추가 — 분해 후 합성."""
    assert compose_hangul("한ㄱㅜㄱ") == "한국"


# ---- 종성 다음 새 음절 (split) ------------------------------------


def test_split_when_jong_then_jung_new_syllable():
    """종성 뒤에 모음이 오면 종성을 다음 음절의 초성으로 split.

    예: '한국어' 처럼 'ㄱ'(종성) 다음 'ㅓ' 가 오면 사실 'ㄱ'(초성).
    """
    assert compose_hangul("ㅎㅏㄴㄱㅜㄱㅇㅓ") == "한국어"


# ---- Hangul Jamo direct (U+1100~) -----------------------------------


def test_compose_hangul_jamo_direct():
    """Compat Jamo 가 아닌 Hangul Jamo (U+1100~) 직접 입력도 처리."""
    src = "한"  # ㅎ ㅏ ㄴ Hangul Jamo
    assert compose_hangul(src) == "한"


# ---- 비한글 unicode passthrough -------------------------------------


def test_other_unicode_passthrough():
    assert compose_hangul("日本") == "日本"
    assert compose_hangul("🎉") == "🎉"
    assert compose_hangul("한 hello 日") == "한 hello 日"
