"""dashboard.py — gather_stats / render_dashboard / _fmt_bytes."""

from __future__ import annotations

import pytest

from whooing_core import attachments as core_attach
from whooing_core import db as core_db
from whooing_tui import data as tui_data
from whooing_tui.screens.dashboard import (
    _fmt_bytes,
    gather_stats,
    render_dashboard,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    return tmp_path


# ---- _fmt_bytes ---------------------------------------------------


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (512, "512 B"),
    (1024, "1.0 KB"),
    (1024 * 1024, "1.0 MB"),
    (1024 * 1024 * 1024, "1.00 GB"),
])
def test_fmt_bytes(n, expected):
    assert _fmt_bytes(n) == expected


# ---- gather_stats -------------------------------------------------


def test_gather_stats_empty_db_path_includes_default(isolated):
    """db 가 아예 없는 상태 — schema_version None, count 0."""
    stats = gather_stats()
    assert stats["schema_version"] is None
    assert stats["import_total"] == 0
    assert stats["annotation_count"] == 0
    assert stats["attachment_count"] == 0
    assert "whooing-data.sqlite" in stats["db_path"]


def test_gather_stats_after_init_returns_zeroes(isolated):
    tui_data.init_shared_schema()
    stats = gather_stats()
    assert stats["schema_version"] == 7
    assert stats["import_total"] == 0
    assert stats["annotation_count"] == 0
    assert stats["attachment_count"] == 0


def test_gather_stats_with_data(isolated, tmp_path):
    tui_data.init_shared_schema()
    src = tmp_path / "x.pdf"
    src.write_bytes(b"PDF")
    with tui_data.open_rw() as conn:
        # annotation + tags
        core_db.upsert_annotation(conn, entry_id="e1", section_id="s1", note="m")
        core_db.set_hashtags(conn, "e1", ["식비", "카페"])
        core_db.upsert_annotation(conn, entry_id="e2", section_id="s1", note=None)
        core_db.set_hashtags(conn, "e2", ["식비"])
        # import_log row
        core_db.log_import(
            conn, source_file="/tmp/x.html", source_kind="html",
            statement_period_start="20260501", statement_period_end="20260531",
            issuer="hyundaicard_secure_mail", card_label=None,
            entry_date="20260501", merchant="스타벅스",
            original_amount=5000, fee_amount=0, total_amount=5000,
            currency="KRW", foreign_amount=None, exchange_rate=None,
            section_id="s9046", l_account_id="x50", r_account_id="x153",
            whooing_entry_id="e_real", status="inserted",
            error_message=None, notes=None,
        )
    # attachment via storage layer
    copied, sha, size = core_attach.copy_to_attachments(
        src, attachments_root=tui_data.attachments_root(), attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(tui_data.attachments_root()))
    with tui_data.open_rw() as conn:
        core_attach.upsert_attachment(
            conn, entry_id="e1", section_id="s1",
            file_path=rel, original_path=str(src),
            original_filename="x.pdf",
            file_size_bytes=size, file_sha256=sha,
            mime_type="application/pdf", note=None,
        )

    stats = gather_stats()
    assert stats["schema_version"] == 7
    assert stats["import_total"] == 1
    assert stats["import_by_status"]["inserted"] == 1
    assert stats["annotation_count"] == 2
    assert stats["annotation_with_memo"] == 1  # 'm' for e1, None for e2
    assert ("식비", 2) in stats["top_hashtags"]
    assert stats["attachment_count"] == 1
    assert stats["attachment_unique_files"] == 1
    assert stats["attachment_total_bytes"] == size


# ---- render_dashboard --------------------------------------------


def test_render_dashboard_includes_path_and_version(isolated):
    tui_data.init_shared_schema()
    s = render_dashboard(gather_stats())
    assert "schema" in s
    assert "v7" in s
    assert "whooing-data.sqlite" in s


def test_render_dashboard_no_db(isolated):
    s = render_dashboard(gather_stats())
    assert "db 미생성" in s


# ---- CL #51133+ (H2) section 필터 ---------------------------------------


def test_gather_stats_section_filter_isolates_hashtags(isolated):
    """`section_id` 명시 시 그 섹션의 hashtag/annotation 만 집계."""
    tui_data.init_shared_schema()
    with tui_data.open_rw() as conn:
        core_db.upsert_annotation(conn, entry_id="e1", section_id="s9046", note="m1")
        core_db.set_hashtags(conn, "e1", ["식비"])
        core_db.upsert_annotation(conn, entry_id="e2", section_id="s133178", note="m2")
        core_db.set_hashtags(conn, "e2", ["테스트", "식비"])
    # 전체 — 종전 동작.
    all_stats = gather_stats()
    assert dict(all_stats["top_hashtags"]) == {"식비": 2, "테스트": 1}
    assert all_stats["annotation_count"] == 2
    # s9046 만.
    s9046 = gather_stats(section_id="s9046")
    assert dict(s9046["top_hashtags"]) == {"식비": 1}
    assert s9046["annotation_count"] == 1
    # s133178 만.
    s133178 = gather_stats(section_id="s133178")
    assert dict(s133178["top_hashtags"]) == {"식비": 1, "테스트": 1}
    assert s133178["annotation_count"] == 1
