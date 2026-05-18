"""entries_cache.py — schema v8 영구 sqlite 캐시 단위 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from whooing_core import db as core_db
from whooing_core.entries_cache import (
    cached_count,
    cached_oldest_date,
    list_cached,
    purge_section,
    remove_entry,
    upsert_entries,
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    """isolated db + schema 초기화."""
    p = tmp_path / "test.sqlite"
    core_db.init_schema(p)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _sample(eid: str, *, date: str = "20260518", money: int = 1000,
            left: str = "x20", right: str = "x11",
            item: str = "스타벅스", memo: str = "") -> dict:
    return {
        "entry_id": eid, "entry_date": date,
        "money": money,
        "l_account": "expenses", "l_account_id": left,
        "r_account": "assets", "r_account_id": right,
        "item": item, "memo": memo,
    }


# ---- schema_v8: 테이블 + 인덱스 존재 -----------------------------------


def test_entries_cache_table_exists(conn):
    """schema v8 의 entries_cache 테이블이 init_schema 후 존재."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='entries_cache'"
    ).fetchall()
    assert rows


def test_schema_version_is_8():
    """SCHEMA_VERSION 이 8 로 bump."""
    assert core_db.SCHEMA_VERSION == 8


# ---- upsert_entries ---------------------------------------------------


def test_upsert_basic(conn):
    n = upsert_entries(conn, "s1", [_sample("e1"), _sample("e2")])
    assert n == 2
    assert cached_count(conn, "s1") == 2


def test_upsert_skips_entries_without_id(conn):
    bad = [{"entry_date": "20260518", "money": 100}, _sample("e1")]
    n = upsert_entries(conn, "s1", bad)
    assert n == 1


def test_upsert_replaces_same_pk(conn):
    """같은 (section_id, entry_id) 다시 upsert 시 update."""
    upsert_entries(conn, "s1", [_sample("e1", money=1000, item="A")])
    upsert_entries(conn, "s1", [_sample("e1", money=2000, item="B")])
    rows = list_cached(conn, "s1")
    assert len(rows) == 1
    assert rows[0]["money"] == 2000
    assert rows[0]["item"] == "B"


def test_upsert_isolation_between_sections(conn):
    """같은 entry_id 라도 다른 section_id 면 별개 row."""
    upsert_entries(conn, "s1", [_sample("e1", item="A")])
    upsert_entries(conn, "s2", [_sample("e1", item="B")])
    assert cached_count(conn, "s1") == 1
    assert cached_count(conn, "s2") == 1
    rows_s1 = list_cached(conn, "s1")
    rows_s2 = list_cached(conn, "s2")
    assert rows_s1[0]["item"] == "A"
    assert rows_s2[0]["item"] == "B"


def test_upsert_handles_float_money(conn):
    """money 가 float / str 로 와도 정수 normalize."""
    e = _sample("e1")
    e["money"] = "12345"
    upsert_entries(conn, "s1", [e])
    e2 = _sample("e2")
    e2["money"] = 999.7
    upsert_entries(conn, "s1", [e2])
    rows = list_cached(conn, "s1")
    monies = sorted(r["money"] for r in rows)
    assert monies == [999, 12345]


def test_upsert_returns_zero_for_empty(conn):
    assert upsert_entries(conn, "s1", []) == 0


# ---- list_cached -----------------------------------------------------


def test_list_cached_orders_by_date_desc(conn):
    upsert_entries(conn, "s1", [
        _sample("e1", date="20260510"),
        _sample("e2", date="20260518"),
        _sample("e3", date="20260515"),
    ])
    rows = list_cached(conn, "s1")
    assert [r["entry_id"] for r in rows] == ["e2", "e3", "e1"]


def test_list_cached_date_range_filter(conn):
    upsert_entries(conn, "s1", [
        _sample("e1", date="20260101"),
        _sample("e2", date="20260301"),
        _sample("e3", date="20260518"),
    ])
    # 2~4월 윈도우
    rows = list_cached(conn, "s1",
                       start_date="20260201", end_date="20260430")
    ids = [r["entry_id"] for r in rows]
    assert ids == ["e2"]


def test_list_cached_handles_subindex_dates(conn):
    """후잉의 entry_date 가 '20260518.0001' 같은 sub-index 포함 — substr
    비교라 정상 동작."""
    upsert_entries(conn, "s1", [
        _sample("e1", date="20260518.0001"),
        _sample("e2", date="20260518.0042"),
    ])
    rows = list_cached(conn, "s1",
                       start_date="20260518", end_date="20260518")
    assert len(rows) == 2


def test_list_cached_exclude_set(conn):
    """이미 화면에 보유한 entry_id 는 제외 — 중복 행 방지."""
    upsert_entries(conn, "s1", [
        _sample("e1"), _sample("e2"), _sample("e3"),
    ])
    rows = list_cached(conn, "s1", exclude_entry_ids={"e1", "e2"})
    assert {r["entry_id"] for r in rows} == {"e3"}


def test_list_cached_preserves_extra_fields_from_raw_json(conn):
    """후잉 응답의 추가 필드 (예: l_account 한글명, 라벨, sub_id 등) 보존."""
    raw = _sample("e1")
    raw["sub_id"] = 7
    raw["extra"] = {"foo": "bar"}
    upsert_entries(conn, "s1", [raw])
    rows = list_cached(conn, "s1")
    assert rows[0].get("sub_id") == 7
    assert rows[0].get("extra") == {"foo": "bar"}
    # 캐시 메타도 노출.
    assert "_cache_fetched_at" in rows[0]


# ---- cached_oldest_date / cached_count -------------------------------


def test_cached_oldest_date_returns_min_yyyymmdd(conn):
    upsert_entries(conn, "s1", [
        _sample("e1", date="20260518.0001"),
        _sample("e2", date="20260101"),
        _sample("e3", date="20260301.0099"),
    ])
    assert cached_oldest_date(conn, "s1") == "20260101"


def test_cached_oldest_date_empty(conn):
    assert cached_oldest_date(conn, "s_empty") is None


def test_cached_count_basic(conn):
    upsert_entries(conn, "s1", [_sample("e1"), _sample("e2")])
    upsert_entries(conn, "s2", [_sample("e3")])
    assert cached_count(conn, "s1") == 2
    assert cached_count(conn, "s2") == 1
    assert cached_count(conn, "s_none") == 0


# ---- purge / remove --------------------------------------------------


def test_purge_section_deletes_all_rows_in_section(conn):
    upsert_entries(conn, "s1", [_sample("e1"), _sample("e2")])
    upsert_entries(conn, "s2", [_sample("e3")])
    n = purge_section(conn, "s1")
    assert n == 2
    assert cached_count(conn, "s1") == 0
    assert cached_count(conn, "s2") == 1  # 다른 섹션은 영향 X


def test_remove_entry_single_row(conn):
    upsert_entries(conn, "s1", [_sample("e1"), _sample("e2")])
    assert remove_entry(conn, "s1", "e1") is True
    assert cached_count(conn, "s1") == 1
    # 없는 entry_id 면 False
    assert remove_entry(conn, "s1", "e_unknown") is False
