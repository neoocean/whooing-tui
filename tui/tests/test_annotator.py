"""annotator.py — input parsing + db 저장 round-trip."""

from __future__ import annotations

import pytest

from whooing_core import db as core_db
from whooing_tui import data as tui_data
from whooing_tui.screens.annotator import (
    load_existing_annotation,
    parse_hashtags_input,
)


# ---- parse_hashtags_input -------------------------------------------


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


# ---- load_existing_annotation ---------------------------------------


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    tui_data.init_shared_schema()
    return tmp_path


def test_load_existing_returns_empty_for_unknown(isolated):
    note, tags = load_existing_annotation("e_unknown")
    assert note is None
    assert tags == []


def test_load_existing_returns_saved(isolated):
    with tui_data.open_rw() as conn:
        core_db.upsert_annotation(
            conn, entry_id="e1", section_id="s1", note="테스트 메모",
        )
        core_db.set_hashtags(conn, "e1", ["식비", "카페"])
    note, tags = load_existing_annotation("e1")
    assert note == "테스트 메모"
    assert sorted(tags) == ["식비", "카페"]


def test_load_existing_handles_missing_db(tmp_path, monkeypatch):
    """DB 가 아예 없는 환경 — graceful degrade."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path / "nonexistent"))
    note, tags = load_existing_annotation("e1")
    assert note is None
    assert tags == []
