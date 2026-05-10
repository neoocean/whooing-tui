"""parse_hashtags_input 단위 테스트.

CL #51137+ (H1): AnnotatorModal 제거됨 — 본 함수는 `edit_entry.py` 에 동일
구현이 살아있고 `whooing_tui.screens.parse_hashtags_input` 으로 re-export.
"""

from __future__ import annotations

from whooing_tui.screens import parse_hashtags_input


def test_parse_hashtags_empty():
    assert parse_hashtags_input("") == []
    assert parse_hashtags_input("   ") == []


def test_parse_hashtags_with_hash_prefix():
    assert parse_hashtags_input("#식비 #카페") == ["식비", "카페"]


def test_parse_hashtags_without_hash_prefix():
    assert parse_hashtags_input("식비 카페") == ["식비", "카페"]


def test_parse_hashtags_comma_separated():
    assert parse_hashtags_input("식비,카페,#서울") == ["식비", "카페", "서울"]


def test_parse_hashtags_dedups():
    assert parse_hashtags_input("#식비 #식비 식비") == ["식비"]


def test_parse_hashtags_preserves_order():
    assert parse_hashtags_input("#출장 #서울 #식비") == ["출장", "서울", "식비"]
