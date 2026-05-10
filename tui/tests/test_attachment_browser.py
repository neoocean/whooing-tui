"""attachment_browser.py — db round-trip + helper tests."""

from __future__ import annotations

import pytest

from whooing_tui import data as tui_data
from whooing_tui.screens.attachment_browser import (
    _fmt_bytes,
    add_attachment,
    list_for,
    remove,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    tui_data.init_shared_schema()
    return tmp_path


@pytest.fixture
def src_file(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"%PDF-fake")
    return p


# ---- _fmt_bytes -----------------------------------------------------


def test_fmt_bytes_none():
    assert _fmt_bytes(None) == ""
    assert _fmt_bytes(0) == ""


def test_fmt_bytes_small():
    assert _fmt_bytes(123) == "123 B"


def test_fmt_bytes_kb():
    assert _fmt_bytes(2048) == "2.0 KB"


def test_fmt_bytes_mb():
    assert _fmt_bytes(2 * 1024 * 1024) == "2.0 MB"


# ---- add_attachment / list_for / remove ---------------------------


def test_list_empty(isolated):
    assert list_for("e_unknown") == []


def test_add_then_list(isolated, src_file):
    row = add_attachment("e1", str(src_file), note="첫 첨부")
    assert row["entry_id"] == "e1"
    assert row["original_filename"] == "invoice.pdf"
    assert row["note"] == "첫 첨부"
    rows = list_for("e1")
    assert len(rows) == 1


def test_add_dedups_same_entry_same_file(isolated, src_file):
    add_attachment("e1", str(src_file))
    add_attachment("e1", str(src_file))  # 같은 sha256 → existing row 재사용
    assert len(list_for("e1")) == 1


def test_add_separate_entries_share_disk(isolated, src_file):
    """같은 sha256 이지만 entry 다르면 row 2개. 디스크 파일은 1개."""
    add_attachment("e1", str(src_file))
    add_attachment("e2", str(src_file))
    assert len(list_for("e1")) == 1
    assert len(list_for("e2")) == 1


def test_remove_returns_dict_when_exists(isolated, src_file):
    row = add_attachment("e1", str(src_file))
    deleted = remove(row["id"], delete_file=True)
    assert deleted is not None
    assert list_for("e1") == []


def test_remove_returns_none_when_missing(isolated):
    assert remove(99999) is None


def test_add_missing_source_raises(isolated, tmp_path):
    with pytest.raises(FileNotFoundError):
        add_attachment("e1", str(tmp_path / "nope.pdf"))


def test_add_directory_rejected(isolated, tmp_path):
    with pytest.raises(ValueError):
        add_attachment("e1", str(tmp_path))
