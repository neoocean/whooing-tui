"""revisions.py + schema v10 — 거래 수정 이력/소프트삭제(안 B) 단위 테스트.

설계: docs/scenarios/11-edit-history-and-soft-delete.md
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from whooing_core import db as core_db
from whooing_core import revisions as rev


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    p = tmp_path / "test.sqlite"
    core_db.init_schema(p)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _entry(eid="1001", *, date="20260601", money=30000, item="저녁",
           left="x50", right="x80", memo="") -> dict:
    return {
        "entry_id": eid, "entry_date": date, "money": money,
        "l_account": "expenses", "l_account_id": left,
        "r_account": "liabilities", "r_account_id": right,
        "item": item, "memo": memo,
    }


# ---- 스키마 ------------------------------------------------------------


def test_schema_v10_tables_exist(conn):
    assert core_db.SCHEMA_VERSION >= 10
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "entry_revisions" in tables
    assert "entry_head" in tables


# ---- 순수 헬퍼 ---------------------------------------------------------


def test_snapshot_fields_coerces_money():
    snap = rev.snapshot_fields(_entry(money="30000"))
    assert snap["money"] == 30000
    assert set(snap) == set(rev.SNAPSHOT_FIELDS)


def test_diff_and_summary():
    a = rev.snapshot_fields(_entry(money=30000, item="저녁"))
    b = rev.snapshot_fields(_entry(money=27000, item="저녁(닭칼국수)"))
    changes = rev.diff(a, b)
    fields = {c[0] for c in changes}
    assert fields == {"money", "item"}
    s = rev.summarize_diff(changes)
    assert "money 30,000→27,000" in s
    assert rev.summarize_diff([]) == "(변경 없음)"


# ---- record_revision + head -------------------------------------------


def test_baseline_then_edit_keeps_logical_id(conn):
    lid = rev.ensure_baseline(conn, entry=_entry("1001"), section_id="s1")
    assert lid == "1001"
    # 재호출 — 이미 추적 중이면 같은 logical_id, 새 버전 안 생김.
    assert rev.ensure_baseline(conn, entry=_entry("1001"), section_id="s1") == "1001"
    assert len(rev.list_revisions(conn, lid)) == 1

    # 수정: 제자리 갱신 → whooing_entry_id 동일.
    rev.record_revision(
        conn, logical_id=lid, whooing_entry_id="1001", section_id="s1",
        op=rev.OP_EDIT, snapshot=rev.snapshot_fields(_entry("1001", money=27000)),
    )
    revs = rev.list_revisions(conn, lid)
    assert [r["revision_no"] for r in revs] == [1, 2]
    assert [r["op"] for r in revs] == ["create", "edit"]
    head = rev.head_for(conn, lid)
    assert head["current_entry_id"] == "1001"
    assert head["is_deleted"] == 0
    assert head["head_revision_no"] == 2
    assert rev.logical_id_for_entry(conn, "1001") == lid


def test_soft_delete_then_restore_new_entry_id(conn):
    """안 B: 삭제 시 head.current=NULL/is_deleted, 복원 시 새 entry_id 로 재생성."""
    lid = rev.ensure_baseline(conn, entry=_entry("1001"), section_id="s1")

    # 삭제(안 B): 후잉 실삭제 → whooing_entry_id=None, is_deleted.
    rev.record_revision(
        conn, logical_id=lid, whooing_entry_id=None, section_id="s1",
        op=rev.OP_DELETE, snapshot=rev.snapshot_fields(_entry("1001")),
        is_deleted=True,
    )
    head = rev.head_for(conn, lid)
    assert head["is_deleted"] == 1
    assert head["current_entry_id"] is None
    # 옛 entry_id 로도 logical 역추적 가능(revision fallback).
    assert rev.logical_id_for_entry(conn, "1001") == lid
    # 휴지통 목록에 등장 + 마지막 스냅샷 노출.
    trash = rev.list_deleted(conn, "s1")
    assert [t["logical_id"] for t in trash] == [lid]
    assert trash[0]["money"] == 30000

    # 복원: 후잉 재생성 → 새 entry_id "2002".
    rev.record_revision(
        conn, logical_id=lid, whooing_entry_id="2002", section_id="s1",
        op=rev.OP_RESTORE, snapshot=rev.snapshot_fields(_entry("2002")),
        is_deleted=False, reverted_from=1,
    )
    head = rev.head_for(conn, lid)
    assert head["is_deleted"] == 0
    assert head["current_entry_id"] == "2002"
    # 새 후잉 id → 같은 logical_id 로 매핑.
    assert rev.logical_id_for_entry(conn, "2002") == lid
    assert rev.list_deleted(conn, "s1") == []
    assert [r["op"] for r in rev.list_revisions(conn, lid)] == [
        "create", "delete", "restore"
    ]


def test_purge_logical_removes_all(conn):
    lid = rev.ensure_baseline(conn, entry=_entry("1001"), section_id="s1")
    rev.record_revision(
        conn, logical_id=lid, whooing_entry_id=None, section_id="s1",
        op=rev.OP_DELETE, snapshot=rev.snapshot_fields(_entry("1001")),
        is_deleted=True,
    )
    n = rev.purge_logical(conn, lid)
    assert n == 2
    assert rev.list_revisions(conn, lid) == []
    assert rev.head_for(conn, lid) is None


def test_record_revision_rejects_unknown_op(conn):
    with pytest.raises(ValueError):
        rev.record_revision(
            conn, logical_id="x", whooing_entry_id="x", section_id="s1",
            op="bogus", snapshot=rev.snapshot_fields(_entry()),
        )
