"""EntryRepository — 로컬 sqlite 어댑터 unit tests.

CL #52834+. 종전엔 EntriesScreen 안의 `_persist_local` / `_purge_local` /
`_fetch_*` 가 직접 db 를 호출 — 본 repo 가 같은 책임을 캡슐화.
"""

from __future__ import annotations

import pytest

from whooing_tui.repository import EntryRepository


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """conftest 의 fixture 와 동일 — WHOOING_DATA_DIR 격리."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path / "whooing"))
    yield


def test_tags_for_returns_empty_when_no_db():
    """db 가 아직 없으면 빈 list — 폼이 열리는 흐름 안 막음."""
    repo = EntryRepository()
    assert repo.tags_for("e1") == []


def test_tags_for_returns_empty_when_entry_id_empty():
    repo = EntryRepository()
    assert repo.tags_for("") == []


def test_tags_for_many_returns_empty_when_input_empty():
    repo = EntryRepository()
    assert repo.tags_for_many([]) == {}


def test_all_tags_returns_empty_when_no_db():
    repo = EntryRepository()
    assert repo.all_tags() == {}


def test_attachment_counts_returns_empty_when_input_empty():
    repo = EntryRepository()
    assert repo.attachment_counts([]) == {}


def test_tag_colors_returns_empty_when_no_db():
    repo = EntryRepository()
    assert repo.tag_colors() == {}


def test_persist_then_tags_for_roundtrip():
    """persist 후 tags_for 가 같은 태그 반환 — 라운드트립."""
    from whooing_tui import data as tui_data
    tui_data.init_shared_schema()

    repo = EntryRepository()
    repo.persist(
        entry_id="e-roundtrip",
        section_id="s1",
        memo="memo 테스트",
        tags=["식비", "외식"],
    )
    tags = repo.tags_for("e-roundtrip")
    assert sorted(tags) == ["식비", "외식"]


def test_persist_then_purge_clears_tags():
    """purge 후 tags_for 가 빈 list 반환."""
    from whooing_tui import data as tui_data
    tui_data.init_shared_schema()

    repo = EntryRepository()
    repo.persist(
        entry_id="e-purge",
        section_id="s1",
        memo="x",
        tags=["a"],
    )
    assert repo.tags_for("e-purge") == ["a"]
    repo.purge("e-purge")
    assert repo.tags_for("e-purge") == []


def test_tags_for_many_filters_to_those_with_tags():
    """태그 0 개 entry 는 결과 dict 에서 빠진다."""
    from whooing_tui import data as tui_data
    tui_data.init_shared_schema()

    repo = EntryRepository()
    repo.persist(entry_id="has", section_id="s1", memo="", tags=["x"])
    repo.persist(entry_id="empty", section_id="s1", memo="", tags=[])
    result = repo.tags_for_many(["has", "empty"])
    assert "has" in result
    assert result["has"] == ["x"]
    assert "empty" not in result


def test_purge_empty_id_is_noop():
    repo = EntryRepository()
    # raise 하지 않음.
    repo.purge("")


def test_persist_empty_id_is_noop():
    repo = EntryRepository()
    repo.persist(entry_id="", section_id="s1", memo="x", tags=["y"])
