"""whooing_core.db — schema + CRUD 단위 테스트."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from whooing_core import db


@pytest.fixture
def fresh_db(tmp_path):
    """schema-initialized 임시 db 반환 (path 만)."""
    p = tmp_path / "test.sqlite"
    db.init_schema(p)
    return p


# ---- schema ------------------------------------------------------------


def test_init_schema_idempotent(tmp_path):
    p = tmp_path / "x.sqlite"
    db.init_schema(p)
    db.init_schema(p)
    assert db.current_version(p) == db.SCHEMA_VERSION


def test_current_version_none_when_db_missing(tmp_path):
    assert db.current_version(tmp_path / "nonexistent.sqlite") is None


def test_current_version_returns_4(fresh_db):
    assert db.current_version(fresh_db) == 4


def test_init_schema_enables_wal(tmp_path):
    p = tmp_path / "wal.sqlite"
    db.init_schema(p)
    conn = sqlite3.connect(str(p))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_open_ro_rejects_write(fresh_db):
    """read-only 연결로 INSERT 시도하면 sqlite3.OperationalError."""
    with db.open_ro(fresh_db) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO entry_annotations "
                "(entry_id, section_id, note, created_at, updated_at) "
                "VALUES ('x', 's', 'm', '2026', '2026')"
            )


def test_open_ro_allows_select(fresh_db):
    with db.open_ro(fresh_db) as conn:
        rows = conn.execute("SELECT * FROM entry_annotations").fetchall()
        assert rows == []


def test_open_ro_raises_when_db_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        with db.open_ro(tmp_path / "nope.sqlite"):
            pass


# ---- annotations CRUD --------------------------------------------------


def test_upsert_annotation_creates_then_updates(fresh_db):
    with db.open_rw(fresh_db) as conn:
        r1 = db.upsert_annotation(conn, entry_id="e1", section_id="s1", note="hi")
        assert r1["note"] == "hi"
        r2 = db.upsert_annotation(conn, entry_id="e1", section_id=None, note="updated")
        assert r2["note"] == "updated"
        assert r2["section_id"] == "s1"  # COALESCE: None 은 기존값 유지


def test_upsert_annotation_note_none_preserves(fresh_db):
    """note=None 으로 호출해도 기존 메모 유지 (해시태그만 변경 시나리오)."""
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note="원본")
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note=None)
        row = conn.execute(
            "SELECT note FROM entry_annotations WHERE entry_id = 'e1'"
        ).fetchone()
        assert row["note"] == "원본"


def test_remove_annotation(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note="hi")
        removed = db.remove_annotation(conn, "e1")
        assert removed["note"] == "hi"
        assert db.remove_annotation(conn, "e1") is None


# ---- hashtags ----------------------------------------------------------


def test_set_hashtags_replaces_all(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id=None, note=None)
        tags1 = db.set_hashtags(conn, "e1", ["식비", "카페"])
        assert sorted(tags1) == ["식비", "카페"]
        tags2 = db.set_hashtags(conn, "e1", ["커피"])
        assert tags2 == ["커피"]
        # all old tags gone
        rows = conn.execute(
            "SELECT tag FROM entry_hashtags WHERE entry_id = 'e1' ORDER BY tag"
        ).fetchall()
        assert [r["tag"] for r in rows] == ["커피"]


def test_set_hashtags_strips_hash_prefix_and_dedups(fresh_db):
    with db.open_rw(fresh_db) as conn:
        tags = db.set_hashtags(conn, "e1", ["#식비", "식비", "  커피 "])
        assert sorted(tags) == ["식비", "커피"]


def test_set_hashtags_empty_clears_all(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e1", [])
        rows = conn.execute("SELECT * FROM entry_hashtags").fetchall()
        assert rows == []


def test_set_hashtags_creates_annotation_row_if_missing(fresh_db):
    """hashtag 만 추가해도 annotation row 가 자동 생성 (FK 보장)."""
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e_new", ["#x"])
        row = conn.execute(
            "SELECT * FROM entry_annotations WHERE entry_id = 'e_new'"
        ).fetchone()
        assert row is not None
        assert row["note"] is None


def test_list_hashtags_returns_counts(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비", "카페"])
        db.set_hashtags(conn, "e2", ["식비"])
        counts = db.list_hashtags(conn)
        assert counts == {"식비": 2, "카페": 1}


def test_find_entries_by_hashtag(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["식비", "외식"])
        db.set_hashtags(conn, "e3", ["카페"])
        ids = db.find_entries_by_hashtag(conn, "식비")
        assert ids == ["e1", "e2"]


def test_get_annotations_for_batch(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note="m1")
        db.set_hashtags(conn, "e1", ["t1", "t2"])
        db.upsert_annotation(conn, entry_id="e2", section_id="s1", note="m2")
        result = db.get_annotations_for(conn, ["e1", "e2", "e_missing"])
        assert "e1" in result
        assert result["e1"]["note"] == "m1"
        assert sorted(result["e1"]["hashtags"]) == ["t1", "t2"]
        assert result["e2"]["note"] == "m2"
        assert "e_missing" not in result


# ---- import_log --------------------------------------------------------


def test_log_import_basic(fresh_db):
    with db.open_rw(fresh_db) as conn:
        log_id = db.log_import(
            conn,
            source_file="/tmp/x.html",
            source_kind="html",
            statement_period_start="20260401",
            statement_period_end="20260430",
            issuer="hyundaicard_secure_mail",
            card_label="[우진]현대카드",
            entry_date="20260415",
            merchant="스타벅스",
            original_amount=5000,
            fee_amount=0,
            total_amount=5000,
            currency="KRW",
            foreign_amount=None,
            exchange_rate=None,
            section_id="s9046",
            l_account_id="x50",
            r_account_id="x153",
            whooing_entry_id="e_new",
            status="inserted",
            error_message=None,
            notes=None,
        )
        assert log_id > 0
        row = conn.execute(
            "SELECT * FROM statement_import_log WHERE id = ?", (log_id,)
        ).fetchone()
        assert row["status"] == "inserted"
        assert row["whooing_entry_id"] == "e_new"
