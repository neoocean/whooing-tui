"""whooing_tui.data — path resolution + schema init + RO/RW 연결."""

from __future__ import annotations

import sqlite3

import pytest

from whooing_tui import data


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """WHOOING_DATA_DIR 를 tmp 로 격리 — 실 ~/.whooing 손대지 않음."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    return tmp_path


def test_data_dir_uses_env(isolated_data_dir):
    assert data.data_dir() == isolated_data_dir


def test_data_dir_default_when_env_missing(monkeypatch):
    monkeypatch.delenv("WHOOING_DATA_DIR", raising=False)
    assert str(data.data_dir()).endswith(".whooing")


def test_db_path_inside_data_dir(isolated_data_dir):
    assert data.db_path() == isolated_data_dir / "whooing-data.sqlite"


def test_attachments_root_inside_data_dir(isolated_data_dir):
    assert data.attachments_root() == isolated_data_dir / "attachments"


def test_init_shared_schema_creates_db_and_dirs(isolated_data_dir):
    p = data.init_shared_schema()
    assert p == isolated_data_dir / "whooing-data.sqlite"
    assert p.exists()
    assert (isolated_data_dir / "attachments").exists()
    assert data.schema_version() == 4


def test_init_shared_schema_idempotent(isolated_data_dir):
    data.init_shared_schema()
    data.init_shared_schema()
    assert data.schema_version() == 4


def test_open_rw_yields_sqlite_connection(isolated_data_dir):
    data.init_shared_schema()
    with data.open_rw() as conn:
        assert isinstance(conn, sqlite3.Connection)
        # write should succeed
        conn.execute(
            "INSERT INTO entry_annotations "
            "(entry_id, section_id, note, created_at, updated_at) "
            "VALUES ('e1', 's1', 'test', '2026', '2026')"
        )


def test_open_ro_blocks_write(isolated_data_dir):
    data.init_shared_schema()
    with data.open_ro() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO entry_annotations "
                "(entry_id, section_id, note, created_at, updated_at) "
                "VALUES ('x', 's', 'm', '2026', '2026')"
            )


def test_schema_version_none_when_db_missing(isolated_data_dir):
    # db 가 아예 없으면 None
    assert data.schema_version() is None
