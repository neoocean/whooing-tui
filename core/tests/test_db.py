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
    assert db.current_version(fresh_db) == 7


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


def test_set_hashtags_splits_whitespace_in_input_token(fresh_db):
    """CL #51140+ (H3): caller 가 공백 포함 단일 토큰 보내도 split — 안전망."""
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["식비 외식", "  카페  "])
        assert sorted(out) == sorted(["식비", "외식", "카페"])


def test_set_hashtags_handles_comma_inside_token(fresh_db):
    """콤마도 분리자."""
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["식비,외식,카페"])
        assert sorted(out) == sorted(["식비", "외식", "카페"])


def test_set_hashtags_skips_none_in_list(fresh_db):
    """defensive — None 토큰은 무시."""
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["식비", None, "카페"])  # type: ignore[list-item]
        assert sorted(out) == sorted(["식비", "카페"])


def test_set_hashtags_records_section_id_from_annotation(fresh_db):
    """CL #51133+ (H2): 명시 section_id 없으면 entry_annotations 의 값 사용."""
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s9046", note=None)
        db.set_hashtags(conn, "e1", ["식비"])
        row = conn.execute(
            "SELECT section_id FROM entry_hashtags WHERE entry_id = 'e1' AND tag = '식비'"
        ).fetchone()
        assert row["section_id"] == "s9046"


def test_set_hashtags_explicit_section_overrides(fresh_db):
    """명시 section_id 매개변수가 annotation 의 값보다 우선."""
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s_old", note=None)
        db.set_hashtags(conn, "e1", ["식비"], section_id="s_new")
        row = conn.execute(
            "SELECT section_id FROM entry_hashtags WHERE entry_id = 'e1'"
        ).fetchone()
        assert row["section_id"] == "s_new"


def test_list_hashtags_filters_by_section(fresh_db):
    """CL #51133+ (H2): section_id 명시 시 해당 섹션만, None = 전체."""
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s9046", note=None)
        db.upsert_annotation(conn, entry_id="e2", section_id="s133178", note=None)
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["식비", "테스트"])
        # 전체.
        all_counts = db.list_hashtags(conn)
        assert all_counts == {"식비": 2, "테스트": 1}
        # s9046 만.
        s9046 = db.list_hashtags(conn, section_id="s9046")
        assert s9046 == {"식비": 1}
        # s133178 만.
        s133178 = db.list_hashtags(conn, section_id="s133178")
        assert s133178 == {"식비": 1, "테스트": 1}


def test_find_entries_by_hashtag_filters_by_section(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note=None)
        db.upsert_annotation(conn, entry_id="e2", section_id="s2", note=None)
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["식비"])
        all_ids = db.find_entries_by_hashtag(conn, "식비")
        assert sorted(all_ids) == ["e1", "e2"]
        s1_ids = db.find_entries_by_hashtag(conn, "식비", section_id="s1")
        assert s1_ids == ["e1"]


# ---- CL #51150+ (H4) tag case normalize 옵션 ----------------------------


def test_set_hashtags_default_preserves_case(fresh_db, monkeypatch):
    monkeypatch.delenv("WHOOING_TAG_CASE_NORMALIZE", raising=False)
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["Cafe", "cafe", "한글"])
        # default preserve — 3 별개 (한글은 casefold 영향 X).
        assert sorted(out) == sorted(["Cafe", "cafe", "한글"])


def test_set_hashtags_lower_normalizes_english(fresh_db, monkeypatch):
    monkeypatch.setenv("WHOOING_TAG_CASE_NORMALIZE", "lower")
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["Cafe", "cafe", "ABC"])
        # 모두 소문자 → cafe + abc 2개.
        assert sorted(out) == sorted(["cafe", "abc"])


def test_set_hashtags_upper_normalizes(fresh_db, monkeypatch):
    monkeypatch.setenv("WHOOING_TAG_CASE_NORMALIZE", "upper")
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["Cafe", "cafe", "abc"])
        assert sorted(out) == sorted(["CAFE", "ABC"])


def test_set_hashtags_invalid_mode_falls_back_to_preserve(fresh_db, monkeypatch):
    monkeypatch.setenv("WHOOING_TAG_CASE_NORMALIZE", "weird-value")
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["Cafe", "cafe"])
        assert sorted(out) == sorted(["Cafe", "cafe"])


def test_set_hashtags_lower_korean_unaffected(fresh_db, monkeypatch):
    """한글 음절은 casefold 영향 없음 (Hangul Syllables 는 case 없음)."""
    monkeypatch.setenv("WHOOING_TAG_CASE_NORMALIZE", "lower")
    with db.open_rw(fresh_db) as conn:
        out = db.set_hashtags(conn, "e1", ["식비", "카페"])
        assert sorted(out) == sorted(["식비", "카페"])


# ---- CL #51147+ (A16) attachment_audit_log ------------------------------


# ---- CL #51151+ (H11) tag colors ---------------------------------------


def test_set_tag_color_basic(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_tag_color(conn, "여행", "cyan")
        colors = db.get_tag_colors(conn)
        assert colors == {"여행": "cyan"}


def test_set_tag_color_strips_hash_prefix(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_tag_color(conn, "#식비", "red")
        assert db.get_tag_colors(conn) == {"식비": "red"}


def test_set_tag_color_section_specific_overrides_default(fresh_db):
    with db.open_rw(fresh_db) as conn:
        # default (NULL section) = blue.
        db.set_tag_color(conn, "여행", "blue")
        # s1 의 specific override = red.
        db.set_tag_color(conn, "여행", "red", section_id="s1")
        # s1 조회 — red.
        c1 = db.get_tag_colors(conn, section_id="s1")
        assert c1["여행"] == "red"
        # s2 조회 — default (blue) 만.
        c2 = db.get_tag_colors(conn, section_id="s2")
        assert c2["여행"] == "blue"


def test_set_tag_color_none_removes_default(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_tag_color(conn, "여행", "red")
        assert db.get_tag_colors(conn) == {"여행": "red"}
        db.set_tag_color(conn, "여행", None)
        assert db.get_tag_colors(conn) == {}


def test_set_tag_color_empty_string_removes(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_tag_color(conn, "여행", "red")
        db.set_tag_color(conn, "여행", "  ")
        assert db.get_tag_colors(conn) == {}


def test_get_tag_colors_returns_empty_when_no_meta(fresh_db):
    with db.open_rw(fresh_db) as conn:
        assert db.get_tag_colors(conn) == {}


def test_log_attachment_audit_inserts_row(fresh_db):
    with db.open_rw(fresh_db) as conn:
        log_id = db.log_attachment_audit(
            conn, attachment_id=42, entry_id="e1", action="add",
            details={"filename": "x.pdf", "size_bytes": 100},
        )
        assert log_id > 0
        row = conn.execute(
            "SELECT * FROM attachment_audit_log WHERE id = ?", (log_id,),
        ).fetchone()
        assert row["action"] == "add"
        assert row["entry_id"] == "e1"
        assert row["attachment_id"] == 42
        # JSON 직렬화 됐어야.
        import json
        d = json.loads(row["details_json"])
        assert d == {"filename": "x.pdf", "size_bytes": 100}


def test_list_attachment_audit_filters_and_orders(fresh_db):
    import time
    with db.open_rw(fresh_db) as conn:
        db.log_attachment_audit(
            conn, attachment_id=1, entry_id="e1", action="add",
        )
        time.sleep(0.01)
        db.log_attachment_audit(
            conn, attachment_id=1, entry_id="e1", action="note_edit",
        )
        time.sleep(0.01)
        db.log_attachment_audit(
            conn, attachment_id=2, entry_id="e2", action="add",
        )
        # entry_id 필터.
        e1 = db.list_attachment_audit(conn, entry_id="e1")
        assert len(e1) == 2
        # 시간 역순 — 최신 (note_edit) 먼저.
        assert e1[0]["action"] == "note_edit"
        assert e1[1]["action"] == "add"
        # attachment_id 필터.
        a2 = db.list_attachment_audit(conn, attachment_id=2)
        assert len(a2) == 1
        assert a2[0]["entry_id"] == "e2"


def test_list_attachment_audit_parses_details_json(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.log_attachment_audit(
            conn, attachment_id=1, entry_id="e1", action="delete",
            details={"trashed": True, "filename": "x.pdf"},
        )
        out = db.list_attachment_audit(conn, attachment_id=1)
        assert len(out) == 1
        assert out[0]["details"] == {"trashed": True, "filename": "x.pdf"}


def test_list_attachment_audit_limit(fresh_db):
    with db.open_rw(fresh_db) as conn:
        for i in range(5):
            db.log_attachment_audit(
                conn, attachment_id=1, entry_id="e1", action="add",
            )
        out = db.list_attachment_audit(conn, attachment_id=1, limit=3)
        assert len(out) == 3


# ---- CL #51145+ (H6) batch add/remove tag -------------------------------


def test_add_tag_to_entries_inserts_for_each(fresh_db):
    with db.open_rw(fresh_db) as conn:
        added = db.add_tag_to_entries(conn, ["e1", "e2", "e3"], "여행")
        assert added == 3
        for eid in ("e1", "e2", "e3"):
            assert db.find_entries_by_hashtag(conn, "여행")
        # annotation row 가 자동 생성됐어야.
        for eid in ("e1", "e2", "e3"):
            row = conn.execute(
                "SELECT 1 FROM entry_annotations WHERE entry_id = ?", (eid,)
            ).fetchone()
            assert row is not None


def test_add_tag_to_entries_skips_existing(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["여행"])
        added = db.add_tag_to_entries(conn, ["e1", "e2"], "여행")
        # e1 이미 있어 skip — 새로 추가된 것은 e2 만.
        assert added == 1


def test_add_tag_strips_hash_prefix(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.add_tag_to_entries(conn, ["e1"], "#출장")
        assert db.find_entries_by_hashtag(conn, "출장") == ["e1"]


def test_add_tag_records_section_id(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s9046", note=None)
        db.add_tag_to_entries(conn, ["e1"], "여행")
        row = conn.execute(
            "SELECT section_id FROM entry_hashtags WHERE entry_id='e1'"
        ).fetchone()
        assert row["section_id"] == "s9046"


def test_remove_tag_from_entries_subset(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["여행", "식비"])
        db.set_hashtags(conn, "e2", ["여행"])
        db.set_hashtags(conn, "e3", ["여행"])
        n = db.remove_tag_from_entries(conn, ["e1", "e2"], "여행")
        assert n == 2
        # e1 의 식비는 보존, 여행만 제거.
        e1_tags = sorted(t["tag"] for t in conn.execute(
            "SELECT tag FROM entry_hashtags WHERE entry_id = 'e1'"
        ).fetchall())
        assert e1_tags == ["식비"]
        # e3 의 여행은 보존 (인자 list 에 없었음).
        assert db.find_entries_by_hashtag(conn, "여행") == ["e3"]


def test_remove_tag_empty_args_returns_zero(fresh_db):
    with db.open_rw(fresh_db) as conn:
        assert db.remove_tag_from_entries(conn, [], "여행") == 0
        assert db.remove_tag_from_entries(conn, ["e1"], "") == 0


# ---- CL #51135+ (H5) rename / merge / delete tag -----------------------


def test_rename_tag_basic(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["식비"])
        result = db.rename_tag(conn, "식비", "외식")
        assert result["renamed"] == 2
        assert result["merged_into_existing"] == 0
        # 모든 entry 가 새 이름.
        assert sorted(db.find_entries_by_hashtag(conn, "외식")) == ["e1", "e2"]
        assert db.find_entries_by_hashtag(conn, "식비") == []


def test_rename_tag_merges_when_dest_exists():
    """새 이름이 이미 있는 entry → old 만 삭제 (PRIMARY KEY 충돌 회피)."""
    import sqlite3
    from whooing_core import db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db._create_tables(conn)
    db._apply_lightweight_migrations(conn)
    db.set_hashtags(conn, "e1", ["식비", "외식"])  # e1 이미 둘 다.
    db.set_hashtags(conn, "e2", ["식비"])           # e2 는 식비만.
    result = db.rename_tag(conn, "식비", "외식")
    assert result["renamed"] == 1            # e2 만 직접 변경.
    assert result["merged_into_existing"] == 1   # e1 의 식비는 dedup.
    e1_tags = sorted(t["tag"] for t in conn.execute(
        "SELECT tag FROM entry_hashtags WHERE entry_id = 'e1'"
    ).fetchall())
    assert e1_tags == ["외식"]


def test_rename_tag_strips_hash_prefix(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비"])
        db.rename_tag(conn, "#식비", "#외식")
        assert db.find_entries_by_hashtag(conn, "외식") == ["e1"]


def test_rename_tag_section_filter(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note=None)
        db.upsert_annotation(conn, entry_id="e2", section_id="s2", note=None)
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["식비"])
        db.rename_tag(conn, "식비", "외식", section_id="s1")
        # s1 만 변경, s2 는 그대로.
        assert db.find_entries_by_hashtag(conn, "외식") == ["e1"]
        assert db.find_entries_by_hashtag(conn, "식비") == ["e2"]


def test_rename_tag_no_op_when_same_name(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비"])
        result = db.rename_tag(conn, "식비", "식비")
        assert result == {"renamed": 0, "merged_into_existing": 0}


def test_merge_tags_combines_multiple_sources(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["외식"])
        db.set_hashtags(conn, "e3", ["점심"])
        result = db.merge_tags(conn, ["식비", "점심"], "외식")
        assert result["sources_processed"] == 2
        assert result["rows_renamed"] == 2
        assert sorted(db.find_entries_by_hashtag(conn, "외식")) == ["e1", "e2", "e3"]
        assert db.find_entries_by_hashtag(conn, "식비") == []
        assert db.find_entries_by_hashtag(conn, "점심") == []


def test_delete_tag_removes_all_rows(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.set_hashtags(conn, "e1", ["식비", "카페"])
        db.set_hashtags(conn, "e2", ["식비"])
        n = db.delete_tag(conn, "식비")
        assert n == 2
        assert db.find_entries_by_hashtag(conn, "식비") == []
        # 다른 태그는 그대로.
        assert db.find_entries_by_hashtag(conn, "카페") == ["e1"]


def test_delete_tag_section_filter(fresh_db):
    with db.open_rw(fresh_db) as conn:
        db.upsert_annotation(conn, entry_id="e1", section_id="s1", note=None)
        db.upsert_annotation(conn, entry_id="e2", section_id="s2", note=None)
        db.set_hashtags(conn, "e1", ["식비"])
        db.set_hashtags(conn, "e2", ["식비"])
        n = db.delete_tag(conn, "식비", section_id="s1")
        assert n == 1
        assert db.find_entries_by_hashtag(conn, "식비") == ["e2"]


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


# ---- CL #51129+ find_imports_by_natural_key ----------------------------


def _seed_log(conn, *, entry_date, merchant, total, status="inserted",
              section="s1", source_file="/tmp/a.csv"):
    return db.log_import(
        conn, source_file=source_file, source_kind="csv",
        statement_period_start=None, statement_period_end=None,
        issuer="x", card_label=None,
        entry_date=entry_date, merchant=merchant,
        original_amount=total, fee_amount=0, total_amount=total,
        currency="KRW", foreign_amount=None, exchange_rate=None,
        section_id=section, l_account_id="x50", r_account_id="x153",
        whooing_entry_id=f"e_{entry_date}_{total}", status=status,
        error_message=None, notes=None,
    )


def test_find_imports_returns_empty_when_no_match(fresh_db):
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="스타벅스", total=5000)
        out = db.find_imports_by_natural_key(
            conn, entry_date="20260416", total_amount=5000, merchant="스타벅스",
        )
        assert out == []


def test_find_imports_matches_natural_key(fresh_db):
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="스타벅스", total=5000)
        out = db.find_imports_by_natural_key(
            conn, entry_date="20260415", total_amount=5000, merchant="스타벅스",
        )
        assert len(out) == 1
        assert out[0]["entry_date"] == "20260415"
        assert out[0]["total_amount"] == 5000


def test_find_imports_filters_by_section(fresh_db):
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="스타벅스",
                  total=5000, section="s1")
        _seed_log(conn, entry_date="20260415", merchant="스타벅스",
                  total=5000, section="s2")
        s1_only = db.find_imports_by_natural_key(
            conn, entry_date="20260415", total_amount=5000, merchant="스타벅스",
            section_id="s1",
        )
        assert len(s1_only) == 1
        assert s1_only[0]["section_id"] == "s1"


def test_find_imports_excludes_failed_status_by_default(fresh_db):
    """default statuses = inserted/matched_existing — failed 는 제외."""
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="x", total=100,
                  status="failed")
        out = db.find_imports_by_natural_key(
            conn, entry_date="20260415", total_amount=100, merchant="x",
        )
        assert out == []


def test_find_imports_respects_custom_statuses(fresh_db):
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="x", total=100,
                  status="failed")
        out = db.find_imports_by_natural_key(
            conn, entry_date="20260415", total_amount=100, merchant="x",
            statuses=("failed",),
        )
        assert len(out) == 1


def test_find_imports_includes_matched_existing(fresh_db):
    """ledger 매칭으로 dedup 됐던 row 도 'matched_existing' 으로 기록 →
    재import 시도 시 다시 매칭."""
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="x", total=100,
                  status="matched_existing")
        out = db.find_imports_by_natural_key(
            conn, entry_date="20260415", total_amount=100, merchant="x",
        )
        assert len(out) == 1
        assert out[0]["status"] == "matched_existing"


def test_find_imports_returns_chronological_order(fresh_db):
    import time
    with db.open_rw(fresh_db) as conn:
        _seed_log(conn, entry_date="20260415", merchant="x", total=100,
                  source_file="/a.csv")
        time.sleep(0.01)  # imported_at 1초 단위라 의미 X — 그래도 안전.
        _seed_log(conn, entry_date="20260415", merchant="x", total=100,
                  source_file="/b.csv")
        out = db.find_imports_by_natural_key(
            conn, entry_date="20260415", total_amount=100, merchant="x",
        )
        assert len(out) == 2
        # ORDER BY imported_at — 같은 timestamp 면 insert 순서 유지.
        assert out[0]["source_file"] == "/a.csv"
