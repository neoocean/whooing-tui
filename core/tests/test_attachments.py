"""whooing_core.attachments — sha256 dedup storage 단위 테스트.

db CRUD (entry_attachments) 는 db.py + tools 에서 검증. 본 테스트는 storage 만.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_core import attachments


@pytest.fixture
def tmp_root(tmp_path):
    """attachments_root 로 쓸 깨끗한 임시 dir."""
    root = tmp_path / "attachments"
    root.mkdir()
    return root


@pytest.fixture
def src_file(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"hello world\n")
    return p


# ---- copy_to_attachments ---------------------------------------------


def test_copy_basic(tmp_root, src_file):
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    assert copied.exists()
    assert copied.parent == tmp_root / "2026" / "2026-05-10"
    assert copied.name == "invoice.pdf"
    assert size == src_file.stat().st_size
    # sha256 of "hello world\n"
    assert sha == "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447"


def test_copy_dedup_same_file(tmp_root, src_file):
    """같은 path 재복사 — 1차 카피 그대로 반환."""
    p1, sha1, _ = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    p2, sha2, _ = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    assert p1 == p2
    assert sha1 == sha2


def test_copy_same_name_different_content_gets_sha_suffix(tmp_root, tmp_path):
    """CL #51140+ (A14): 같은 폴더 같은 이름 다른 내용 → sha 8자 prefix suffix.
    (종전 카운터 `-1` → sha 기반 — 더 견고 + 1회 hash 만 필요.)
    """
    a = tmp_path / "a.pdf"
    b = tmp_path / "b" / "a.pdf"
    a.write_bytes(b"AAA")
    b.parent.mkdir()
    b.write_bytes(b"BBB")
    p1, sha1, _ = attachments.copy_to_attachments(
        a, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    p2, sha2, _ = attachments.copy_to_attachments(
        b, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    assert p1.name == "a.pdf"
    # 두 번째는 sha 8자 prefix 가 stem 끝에 붙음 — "a-<sha8>.pdf".
    assert p2.name == f"a-{sha2[:8]}.pdf"
    assert sha1 != sha2


def test_copy_default_today(tmp_root, src_file):
    """attach_date 생략 시 오늘 날짜 폴더로."""
    copied, _, _ = attachments.copy_to_attachments(src_file, attachments_root=tmp_root)
    # parent dir name 이 YYYY-MM-DD 형식
    parts = copied.relative_to(tmp_root).parts
    assert len(parts) == 3  # YYYY/YYYY-MM-DD/file
    assert len(parts[1]) == 10  # YYYY-MM-DD


def test_copy_missing_source_raises(tmp_root, tmp_path):
    with pytest.raises(FileNotFoundError):
        attachments.copy_to_attachments(
            tmp_path / "nope.pdf", attachments_root=tmp_root,
        )


def test_copy_directory_rejected(tmp_root, tmp_path):
    with pytest.raises(ValueError):
        attachments.copy_to_attachments(tmp_path, attachments_root=tmp_root)


# ---- detect_mime ------------------------------------------------------


def test_detect_mime_pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"")
    assert attachments.detect_mime(p) == "application/pdf"


def test_detect_mime_unknown(tmp_path):
    p = tmp_path / "x.totally_unknown_ext_zzz"
    p.write_bytes(b"")
    assert attachments.detect_mime(p) is None


# ---- upsert + list + delete (db rows + storage 함께) -----------------


def test_upsert_and_list(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn,
            entry_id="e1", section_id="s1",
            file_path=rel, original_path=str(src_file),
            original_filename="invoice.pdf",
            file_size_bytes=size, file_sha256=sha,
            mime_type="application/pdf", note=None,
        )
        assert row["entry_id"] == "e1"
        assert row["file_sha256"] == sha
        # dedup: 같은 entry+sha 면 기존 row 반환
        row2 = attachments.upsert_attachment(
            conn,
            entry_id="e1", section_id="s1",
            file_path=rel, original_path=str(src_file),
            original_filename="invoice.pdf",
            file_size_bytes=size, file_sha256=sha,
            mime_type="application/pdf", note="다시 첨부 시도",
        )
        assert row2["id"] == row["id"]
        # list
        m = attachments.list_attachments_for(conn, ["e1"])
        assert len(m["e1"]) == 1


def test_delete_attachment_removes_file_when_no_other_refs(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn,
            entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="invoice.pdf",
            file_size_bytes=size, file_sha256=sha,
            mime_type=None, note=None,
        )
        deleted = attachments.delete_attachment(
            conn, row["id"], attachments_root=tmp_root, delete_file=True,
        )
        assert deleted["file_deleted"] is True
        assert not copied.exists()


def test_delete_attachment_keeps_file_when_other_refs(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        r1 = attachments.upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha, mime_type=None, note=None,
        )
        attachments.upsert_attachment(
            conn, entry_id="e2", section_id=None,
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha, mime_type=None, note=None,
        )
        deleted = attachments.delete_attachment(
            conn, r1["id"], attachments_root=tmp_root, delete_file=True,
        )
        assert deleted.get("file_kept_other_refs") == 1
        assert copied.exists()


def test_delete_nonexistent_returns_none(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        assert attachments.delete_attachment(
            conn, 9999, attachments_root=tmp_root,
        ) is None


# ---- CL #51132+ (A1) purge_attachments_for_entry ------------------------


def _seed_attach(conn, *, entry_id, file_path, sha, name="x.pdf", size=10):
    return attachments.upsert_attachment(
        conn, entry_id=entry_id, section_id=None,
        file_path=file_path, original_path=None, original_filename=name,
        file_size_bytes=size, file_sha256=sha, mime_type=None, note=None,
    )


def test_purge_for_entry_removes_rows_and_files(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e1", file_path=rel, sha=sha, size=size)
        _seed_attach(conn, entry_id="e1", file_path=rel, sha=sha + "x", size=size)
        purged = attachments.purge_attachments_for_entry(
            conn, "e1", attachments_root=tmp_root,
        )
        # 2 row 가 모두 사라짐. 첫 row 의 sha 는 unique 라 파일도 unlink.
        assert len(purged) == 2
        assert any(p.get("file_deleted") for p in purged)
        rows = conn.execute(
            "SELECT * FROM entry_attachments WHERE entry_id = ?", ("e1",),
        ).fetchall()
        assert rows == []
        assert not copied.exists()


def test_purge_for_entry_keeps_files_when_dedup_other_entry(tmp_root, src_file):
    """같은 sha 의 다른 entry 가 있으면 디스크 파일은 보존."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e1", file_path=rel, sha=sha, size=size)
        _seed_attach(conn, entry_id="e2", file_path=rel, sha=sha, size=size)
        purged = attachments.purge_attachments_for_entry(
            conn, "e1", attachments_root=tmp_root,
        )
        assert len(purged) == 1
        assert purged[0].get("file_kept_other_refs") == 1
        assert copied.exists()


def test_purge_for_entry_no_attachments_returns_empty(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        assert attachments.purge_attachments_for_entry(
            conn, "e_unknown", attachments_root=tmp_root,
        ) == []


# ---- CL #51132+ (A2) find / cleanup orphans -----------------------------


def test_find_orphans_returns_rows_not_in_valid_set(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e_alive", file_path=rel, sha=sha, size=size)
        _seed_attach(conn, entry_id="e_dead", file_path=rel, sha=sha + "y", size=size)
        orphans = attachments.find_orphan_attachments(conn, {"e_alive"})
        assert [o["entry_id"] for o in orphans] == ["e_dead"]


def test_find_orphans_empty_valid_set_returns_empty(tmp_root, src_file):
    """안전성: valid set 이 비면 빈 리스트 (전체를 orphan 으로 보지 않음)."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e1", file_path=rel, sha=sha, size=size)
        assert attachments.find_orphan_attachments(conn, set()) == []


def test_cleanup_dry_run_only_lists(tmp_root, src_file):
    """dry_run=True — 후보만 반환, db/file 변경 X."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e_dead", file_path=rel, sha=sha, size=size)
        result = attachments.cleanup_orphan_attachments(
            conn, {"e_alive"}, attachments_root=tmp_root, dry_run=True,
        )
        assert result["orphan_count"] == 1
        assert result["rows_deleted"] == 0
        assert result["files_deleted"] == 0
        # db row 그대로 + 디스크 파일 그대로.
        rows = conn.execute("SELECT COUNT(*) FROM entry_attachments").fetchone()[0]
        assert rows == 1
        assert copied.exists()


def test_cleanup_actual_removes_rows_and_files(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e_dead", file_path=rel, sha=sha, size=size)
        result = attachments.cleanup_orphan_attachments(
            conn, {"e_alive"}, attachments_root=tmp_root,
        )
        assert result["rows_deleted"] == 1
        assert result["files_deleted"] == 1
        assert not copied.exists()


# ---- CL #51141+ (A13) trash 옵션 ----------------------------------------


# ---- CL #51148+ (A3) application-level CHECK ----------------------------


def test_upsert_rejects_missing_entry_id(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        with pytest.raises(ValueError, match="entry_id"):
            attachments.upsert_attachment(
                conn, entry_id="", section_id=None, file_path="x",
                original_path=None, original_filename="x.pdf",
                file_size_bytes=10, file_sha256="abc", mime_type=None, note=None,
            )


def test_upsert_rejects_missing_file_path(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        with pytest.raises(ValueError, match="file_path"):
            attachments.upsert_attachment(
                conn, entry_id="e1", section_id=None, file_path="",
                original_path=None, original_filename="x.pdf",
                file_size_bytes=10, file_sha256="abc", mime_type=None, note=None,
            )


def test_upsert_rejects_null_size_bytes(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        with pytest.raises(ValueError, match="file_size_bytes"):
            attachments.upsert_attachment(
                conn, entry_id="e1", section_id=None, file_path="x",
                original_path=None, original_filename="x.pdf",
                file_size_bytes=None, file_sha256="abc",
                mime_type=None, note=None,
            )


def test_upsert_rejects_negative_size(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        with pytest.raises(ValueError, match="file_size_bytes"):
            attachments.upsert_attachment(
                conn, entry_id="e1", section_id=None, file_path="x",
                original_path=None, original_filename="x.pdf",
                file_size_bytes=-1, file_sha256="abc",
                mime_type=None, note=None,
            )


def test_upsert_rejects_missing_sha(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        with pytest.raises(ValueError, match="file_sha256"):
            attachments.upsert_attachment(
                conn, entry_id="e1", section_id=None, file_path="x",
                original_path=None, original_filename="x.pdf",
                file_size_bytes=10, file_sha256=None,
                mime_type=None, note=None,
            )


def test_upsert_accepts_valid_input(tmp_root):
    """모두 채우면 정상 — 종전 caller 회귀 보호."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn, entry_id="e1", section_id="s1", file_path="2026/2026-05-10/x.pdf",
            original_path="/x.pdf", original_filename="x.pdf",
            file_size_bytes=10, file_sha256="abc",
            mime_type="application/pdf", note=None,
        )
        assert row["entry_id"] == "e1"


# ---- CL #51144+ (A5) section_id 필터 ------------------------------------


def test_list_attachments_filters_by_section(tmp_root, src_file):
    """list_attachments_for(section_id=X) 가 그 섹션 + NULL 만 반환."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        attachments.upsert_attachment(
            conn, entry_id="e1", section_id="s_a",
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha + "1",
            mime_type=None, note=None,
        )
        attachments.upsert_attachment(
            conn, entry_id="e1", section_id="s_b",
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha + "2",
            mime_type=None, note=None,
        )
        # legacy NULL section.
        attachments.upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha + "3",
            mime_type=None, note=None,
        )
        # None → 모두.
        m_all = attachments.list_attachments_for(conn, ["e1"])
        assert len(m_all["e1"]) == 3
        # s_a → s_a 의 1건 + NULL 의 1건 = 2.
        m_a = attachments.list_attachments_for(conn, ["e1"], section_id="s_a")
        assert len(m_a["e1"]) == 2
        sections = {r["section_id"] for r in m_a["e1"]}
        assert sections == {"s_a", None}
        # s_b → 비슷.
        m_b = attachments.list_attachments_for(conn, ["e1"], section_id="s_b")
        assert len(m_b["e1"]) == 2


def test_list_attachments_empty_entry_ids(tmp_root):
    """entry_ids 빈 list → 빈 사전 (section_id 무관)."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        assert attachments.list_attachments_for(conn, [], section_id="s1") == {}


# ---- CL #51143+ (A9) note 사후 편집 -------------------------------------


def test_update_note_changes_value(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="invoice.pdf",
            file_size_bytes=size, file_sha256=sha, mime_type=None, note="초기",
        )
        updated = attachments.update_attachment_note(conn, row["id"], "수정후")
        assert updated is not None
        assert updated["note"] == "수정후"
        # row 자체도 갱신.
        check = conn.execute(
            "SELECT note FROM entry_attachments WHERE id = ?", (row["id"],),
        ).fetchone()
        assert check["note"] == "수정후"


def test_update_note_empty_string_normalizes_to_null(tmp_root, src_file):
    """빈 문자열 / whitespace-only → NULL."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha, mime_type=None, note="초기",
        )
        out = attachments.update_attachment_note(conn, row["id"], "   ")
        assert out is not None
        assert out["note"] is None


def test_update_note_none_input_normalizes_to_null(tmp_root, src_file):
    """None 입력도 NULL."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha, mime_type=None, note="초기",
        )
        out = attachments.update_attachment_note(conn, row["id"], None)
        assert out["note"] is None


def test_update_note_returns_none_when_id_missing(tmp_root):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    with db.open_rw(db_path) as conn:
        assert attachments.update_attachment_note(conn, 99999, "x") is None


def test_update_note_does_not_touch_other_columns(tmp_root, src_file):
    """note 만 갱신 — 다른 컬럼 (file_path / sha256 / size) 보존."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn, entry_id="e1", section_id="s1",
            file_path=rel, original_path="/x", original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha,
            mime_type="application/pdf", note=None,
        )
        out = attachments.update_attachment_note(conn, row["id"], "신규 note")
        assert out["entry_id"] == "e1"
        assert out["section_id"] == "s1"
        assert out["file_path"] == rel
        assert out["original_filename"] == "x.pdf"
        assert out["file_sha256"] == sha
        assert out["file_size_bytes"] == size
        assert out["mime_type"] == "application/pdf"
        assert out["note"] == "신규 note"


def test_delete_with_trash_moves_to_trash_dir(tmp_root, src_file):
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        row = attachments.upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path=rel, original_path=None, original_filename="invoice.pdf",
            file_size_bytes=size, file_sha256=sha, mime_type=None, note=None,
        )
        deleted = attachments.delete_attachment(
            conn, row["id"], attachments_root=tmp_root,
            delete_file=True, trash=True,
        )
        assert deleted.get("file_trashed") is True
        assert "trash_path" in deleted
        # 원본 자리는 비고 휴지통에 있음.
        assert not copied.exists()
        trash = Path(deleted["trash_path"])
        assert trash.exists()
        # `<root>/.trash/YYYYMMDD/<filename>` 형식.
        assert ".trash" in str(trash)


def test_purge_trash_older_than_removes_old_dirs(tmp_root):
    """trash 의 N일 초과 디렉터리 unlink."""
    from datetime import datetime, timedelta
    from whooing_core.dates import KST
    # 31일 전 디렉터리 + 그 안의 파일.
    old = datetime.now(KST) - timedelta(days=31)
    old_dir = tmp_root / ".trash" / old.strftime("%Y%m%d")
    old_dir.mkdir(parents=True)
    old_file = old_dir / "a.pdf"
    old_file.write_bytes(b"old")
    # 오늘 디렉터리 — 보존.
    today = datetime.now(KST).strftime("%Y%m%d")
    new_dir = tmp_root / ".trash" / today
    new_dir.mkdir(parents=True)
    new_file = new_dir / "b.pdf"
    new_file.write_bytes(b"new")

    result = attachments.purge_trash_older_than(tmp_root, days=30)
    assert result["dirs_purged"] == 1
    assert result["files_purged"] == 1
    assert not old_file.exists()
    assert not old_dir.exists()
    assert new_file.exists()  # 보존.


def test_filename_collision_uses_sha_suffix(tmp_root, src_file, tmp_path):
    """CL #51140+ (A14): 같은 폴더 같은 이름 다른 내용 → sha 8글자 suffix."""
    # 1차 카피.
    p1, sha1, _ = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    # 같은 이름 + 다른 내용.
    other = tmp_path / "invoice.pdf"
    other.write_bytes(b"different content!")
    p2, sha2, _ = attachments.copy_to_attachments(
        other, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    assert p1 != p2
    # 두 번째는 sha 8자 prefix 가 stem 에 붙음.
    assert sha2[:8] in p2.stem
    # 둘 다 디스크에 살아있고 내용 다름.
    assert p1.read_bytes() != p2.read_bytes()


def test_cleanup_keeps_files_when_keep_files_false_dedup(tmp_root, src_file):
    """다른 entry 가 같은 sha 로 살아있으면 disk 보존."""
    from whooing_core import db
    db_path = tmp_root.parent / "data.sqlite"
    db.init_schema(db_path)
    copied, sha, size = attachments.copy_to_attachments(
        src_file, attachments_root=tmp_root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tmp_root))
    with db.open_rw(db_path) as conn:
        _seed_attach(conn, entry_id="e_alive", file_path=rel, sha=sha, size=size)
        _seed_attach(conn, entry_id="e_dead", file_path=rel, sha=sha, size=size)
        result = attachments.cleanup_orphan_attachments(
            conn, {"e_alive"}, attachments_root=tmp_root,
        )
        assert result["rows_deleted"] == 1
        assert result["files_deleted"] == 0
        assert result["files_kept_dedup"] == 1
        assert copied.exists()
