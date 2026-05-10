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
    """CL #51107+ default: monorepo 내부에서 실행 중이면 `<project>/db/`,
    외부면 `~/.whooing` fallback."""
    monkeypatch.delenv("WHOOING_DATA_DIR", raising=False)
    p = str(data.data_dir())
    # 본 테스트 환경 (monorepo) 안에서 실행 중이라 project root 의 db.
    assert p.endswith("/db") or p.endswith(".whooing")


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


# ---- CL #51107+ project root + 마이그레이션 -----------------------------


def test_data_dir_uses_project_db_when_no_env(monkeypatch):
    """env 없을 때 monorepo 안에서 실행 중이면 `<project>/db/`."""
    monkeypatch.delenv("WHOOING_DATA_DIR", raising=False)
    p = data.data_dir()
    # 본 monorepo 안 — 프로젝트 root + /db 로 끝나거나 .whooing fallback.
    assert p.name in {"db", ".whooing"}


def test_init_skips_legacy_migration_when_env_set(tmp_path, monkeypatch):
    """WHOOING_DATA_DIR 가 명시 set 이면 legacy 마이그레이션 skip — 실
    사용자 ~/.whooing 의 db 가 테스트 tmp 로 끌려오지 않게."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    # 마이그레이션 함수가 호출되지 않는지 patch 로 검증.
    called = {"yes": False}

    def _spy(target):
        called["yes"] = True

    monkeypatch.setattr(data, "_maybe_migrate_legacy_db", _spy)
    data.init_shared_schema()
    assert called["yes"] is False


def test_init_runs_legacy_migration_when_env_unset(tmp_path, monkeypatch):
    """env 가 unset 이면 마이그레이션 1회 시도 (project/db 또는 ~/.whooing)."""
    monkeypatch.delenv("WHOOING_DATA_DIR", raising=False)
    called = {"yes": False, "target": None}

    def _spy(target):
        called["yes"] = True
        called["target"] = target

    # data_dir 만 tmp 로 redirect — env 없이.
    monkeypatch.setattr(data, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(data, "_maybe_migrate_legacy_db", _spy)
    data.init_shared_schema()
    assert called["yes"] is True
    assert called["target"] == tmp_path
