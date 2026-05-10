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
    assert data.schema_version() == 7


def test_init_shared_schema_idempotent(isolated_data_dir):
    data.init_shared_schema()
    data.init_shared_schema()
    assert data.schema_version() == 7


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


# ---- CL #51123+ 첨부파일 위치 분리 (project/attachment, 단수) ------------


def test_attachments_root_uses_attachments_env(tmp_path, monkeypatch):
    """`$WHOOING_ATTACHMENTS_DIR` 가 있으면 그 값을 그대로 사용 — 첨부 전용
    격리 환경 변수 (CL #51123+)."""
    monkeypatch.setenv("WHOOING_ATTACHMENTS_DIR", str(tmp_path / "att"))
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path / "data"))
    assert data.attachments_root() == tmp_path / "att"


def test_attachments_root_falls_back_to_data_dir_when_only_data_env(
    tmp_path, monkeypatch,
):
    """`$WHOOING_ATTACHMENTS_DIR` 가 없으면 `$WHOOING_DATA_DIR/attachments` —
    기존 471 테스트 / conftest 격리 패턴 backward compat."""
    monkeypatch.delenv("WHOOING_ATTACHMENTS_DIR", raising=False)
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    assert data.attachments_root() == tmp_path / "attachments"


def test_attachments_root_default_is_project_attachment_singular(monkeypatch):
    """env 가 모두 unset 일 때 monorepo 안에서는 `<project_root>/attachment`
    (단수형) 로 떨어진다 — production default. 사용자 요청 CL #51123:
    db/attachment 가 아니라 project root 직속 attachment 디렉터리."""
    monkeypatch.delenv("WHOOING_ATTACHMENTS_DIR", raising=False)
    monkeypatch.delenv("WHOOING_DATA_DIR", raising=False)
    p = data.attachments_root()
    # monorepo 안에서 실행 중이면 이름이 'attachment' (단수, db 와 분리).
    # monorepo 외부 (pip install) fallback 일 때는 ~/.whooing/attachments.
    assert p.name in {"attachment", "attachments"}
    if p.name == "attachment":
        # production default — db 디렉터리 (`db/`) 아래가 절대 아니어야 함.
        assert p.parent.name != "db"
        assert (p.parent / "tui").is_dir() and (p.parent / "core").is_dir()
