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


def test_copy_same_name_different_content_gets_suffix(tmp_root, tmp_path):
    """같은 폴더에 같은 이름이지만 다른 내용 → -1 suffix 자동."""
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
    assert p2.name == "a-1.pdf"
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
