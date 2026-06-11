"""RecurringScanRepository — save / load / update_status / clear /
has_open_scan round-trips.

격리: pytest 의 `tmp_path` + `WHOOING_DATA_DIR` env override 로 본 테스트
프로세스의 sqlite 가 별도 디렉토리. 다른 테스트와 간섭 없음.
"""

from __future__ import annotations

import pytest

from whooing_core.recurring import find_recurring_series

from whooing_tui import data as tui_data
from whooing_tui.recurring_scan_repo import RecurringScanRepository


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    """sqlite db 를 tmp_path 안으로 — 각 테스트 격리 (schema v11 포함)."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    tui_data.init_shared_schema()
    yield tmp_path


def _series_with_gap():
    """월세 시리즈에서 3월이 빠진 RecurringSeries 1건 생성 (gap)."""
    entries = [
        {"entry_id": f"r{i}", "entry_date": f"2026{mm}15", "money": 600000,
         "item": "월세", "l_account_id": "x30", "r_account_id": "x11"}
        for i, mm in enumerate(["01", "02", "04", "05"])
    ]
    series = find_recurring_series(entries, as_of="20260520")
    assert len(series) == 1
    return series


def test_schema_has_recurring_table(db_env):
    assert tui_data.schema_version() == 11
    with tui_data.open_ro() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='recurring_scan_series'"
        ).fetchone()
    assert row is not None


def test_save_and_load_round_trip(db_env):
    repo = RecurringScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20250520", range_end="20260520",
        series=_series_with_gap(),
    )
    assert len(stored) == 1
    s = stored[0]
    assert s.id > 0
    assert s.cadence == "monthly"
    assert s.item == "월세"
    assert s.typical_money == 600000
    assert s.missing[0]["kind"] == "gap"
    assert s.missing[0]["expected_date"] == "20260315"
    assert s.sample.get("item") == "월세"

    loaded = repo.load_open_series(
        section_id="s1", range_start="20250520", range_end="20260520",
    )
    assert len(loaded) == 1
    assert loaded[0].item == "월세"
    assert loaded[0].sample.get("l_account_id") == "x30"


def test_cache_isolation_by_range_and_section(db_env):
    repo = RecurringScanRepository()
    repo.save_scan(section_id="s1", range_start="20250520",
                   range_end="20260520", series=_series_with_gap())
    # 다른 range → 비어 있음.
    assert repo.load_open_series(
        section_id="s1", range_start="20240101", range_end="20260520",
    ) == []
    # 다른 section → 비어 있음.
    assert repo.load_open_series(
        section_id="s2", range_start="20250520", range_end="20260520",
    ) == []


def test_has_open_scan(db_env):
    repo = RecurringScanRepository()
    assert not repo.has_open_scan(
        section_id="s1", range_start="20250520", range_end="20260520")
    repo.save_scan(section_id="s1", range_start="20250520",
                   range_end="20260520", series=_series_with_gap())
    assert repo.has_open_scan(
        section_id="s1", range_start="20250520", range_end="20260520")


def test_update_status_removes_from_open(db_env):
    repo = RecurringScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20250520", range_end="20260520",
        series=_series_with_gap(),
    )
    repo.update_status(stored[0].id, "handled")
    # open 에서는 빠지고 has_open_scan False.
    assert repo.load_open_series(
        section_id="s1", range_start="20250520", range_end="20260520") == []
    assert not repo.has_open_scan(
        section_id="s1", range_start="20250520", range_end="20260520")
    # all_series 에는 handled 로 남음.
    all_s = repo.load_all_series(
        section_id="s1", range_start="20250520", range_end="20260520")
    assert len(all_s) == 1
    assert all_s[0].status == "handled"


def test_update_status_rejects_invalid(db_env):
    repo = RecurringScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20250520", range_end="20260520",
        series=_series_with_gap(),
    )
    with pytest.raises(ValueError):
        repo.update_status(stored[0].id, "bogus")


def test_clear_scan(db_env):
    repo = RecurringScanRepository()
    repo.save_scan(section_id="s1", range_start="20250520",
                   range_end="20260520", series=_series_with_gap())
    n = repo.clear_scan(
        section_id="s1", range_start="20250520", range_end="20260520")
    assert n == 1
    assert repo.load_all_series(
        section_id="s1", range_start="20250520", range_end="20260520") == []
