"""TUI 의 데이터 layer — whooing-core 를 import 해 SQLite + 첨부 storage 관리.

TUI 가 db + attachments 의 owner — schema 생성/마이그레이션 책임. 본래
wrapper (whooing-mcp-server-wrapper) 가 같은 db 를 read-only 로 SELECT
하도록 설계됐으나 **wrapper 는 2026-05-10 종료 (archived)** — 현재 SELECT
경로의 외부 사용자는 없다. `open_ro()` API 는 안전성 / 명분 분리를 위해
그대로 유지 (다른 도구가 미래에 합류할 가능성).

Path 정책 (v0.18.0+, monorepo, CL #51123+):

  db 위치  ($WHOOING_DATA_DIR > <project_root>/db/ > ~/.whooing/):
    whooing-data.sqlite              sqlite db (annotations / hashtags /
                                     entry_attachments / statement_import_log)

  첨부파일 위치  (db 와 분리 — 사용자 요청 CL #51123):
    $WHOOING_ATTACHMENTS_DIR
      > $WHOOING_DATA_DIR/attachments  (테스트 격리 — env set 시 backward compat)
      > <project_root>/attachment/     (production default — db/ 와 분리, 단수)
      > ~/.whooing/attachments         (fallback)
        └─ YYYY/YYYY-MM-DD/<filename>  sha256 dedup file storage

CL #51107 부터 db default 가 `<project>/db/` 로 변경 — db 파일이 P4 에
들어가 변경 이벤트가 발생할 때마다 자동 submit (mutation 추적 / 원격 동기화).
기존 `~/.whooing/whooing-data.sqlite` 가 있으면 1회 마이그레이션.

CL #51123 부터 첨부파일 default 가 `<project>/attachment/` (단수) — db 디렉토리
와 분리. db 파일은 SQLite 단일 binary 라 P4 변경 추적이 단순하지만, 첨부는
바이너리 N 개라 별도 디렉토리에서 별도 lifecycle (사용자 의도). 기존
`<data_dir>/attachments/` 위치는 env override 시에만 유지.
"""

from __future__ import annotations

import logging
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import sqlite3

from whooing_core import db as core_db

log = logging.getLogger(__name__)


def _project_root() -> Path | None:
    """본 모듈이 설치된 monorepo 의 project root 를 자동 탐색.

    `whooing_tui/` 패키지의 `__file__` → `tui/src/whooing_tui/` → `tui/src/`
    → `tui/` → 그 부모가 monorepo root (옆에 `core/` 가 있을 것).

    `tui/` 와 `core/` 가 sibling 인 monorepo 만 매칭. 일반 pip install
    (site-packages 안) 환경에서는 None — `~/.whooing` fallback.
    """
    here = Path(__file__).resolve()
    # tui/src/whooing_tui/data.py → parents: [whooing_tui, src, tui, root]
    for ancestor in here.parents:
        if (ancestor / "tui").is_dir() and (ancestor / "core").is_dir():
            return ancestor
    return None


def _legacy_home_dir() -> Path:
    """0.14.0 까지의 default 위치 — `~/.whooing/`. 마이그레이션 source."""
    return Path("~/.whooing").expanduser()


def data_dir() -> Path:
    """공유 데이터 root.

    우선순위 (CL #51107+):
      1. `$WHOOING_DATA_DIR` (명시 override — 테스트도 이쪽).
      2. `<project_root>/db/` (monorepo 안에서 실행 시).
      3. `~/.whooing/` (pip install / monorepo 외 fallback).
    """
    explicit = os.getenv("WHOOING_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    project = _project_root()
    if project is not None:
        return project / "db"
    return _legacy_home_dir()


def _maybe_migrate_legacy_db(target: Path) -> None:
    """기존 `~/.whooing/whooing-data.sqlite` 가 있고 target 에 db 가 없으면
    1회 복사. db 만 (attachments 는 별도). 이미 target 에 있으면 noop.
    """
    target_db = target / "whooing-data.sqlite"
    if target_db.exists():
        return
    legacy = _legacy_home_dir() / "whooing-data.sqlite"
    if not legacy.exists() or legacy == target_db:
        return
    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(legacy, target_db)
        # WAL/SHM 보조 파일도 같이 (있다면) — 안전.
        for suffix in ("-wal", "-shm"):
            src = legacy.with_name(legacy.name + suffix)
            if src.exists():
                shutil.copy2(src, target_db.with_name(target_db.name + suffix))
        log.info("legacy db 마이그레이션: %s → %s", legacy, target_db)
    except Exception:  # pragma: no cover — 권한 등 — fatal 이 아니다.
        log.exception("legacy db migration failed; using empty new db")


def db_path() -> Path:
    """SQLite db 의 절대 경로."""
    return data_dir() / "whooing-data.sqlite"


def attachments_root() -> Path:
    """첨부 storage root (TUI write, wrapper read-only).

    우선순위 (CL #51123+):
      1. `$WHOOING_ATTACHMENTS_DIR` (명시 override — 첨부 전용 격리).
      2. `$WHOOING_DATA_DIR/attachments` — data dir 만 격리할 때 (테스트
         conftest 의 일반 패턴) backward compat 로 그 안에 attachments 를
         둔다. 기존 471 테스트가 이 path 를 가정하므로 깨지면 안 됨.
      3. `<project_root>/attachment` — production default. **단수형** (`attachment`)
         이고 `db/` 와 분리. 사용자 요청 CL #51123: "첨부파일은 프로젝트
         루트 하위의 attachment 입니다 ... 첨부파일은 db/attachment 하위가
         아닙니다."
      4. `~/.whooing/attachments` — monorepo 외부 (pip install) fallback.
    """
    explicit = os.getenv("WHOOING_ATTACHMENTS_DIR")
    if explicit:
        return Path(explicit).expanduser()
    explicit_data = os.getenv("WHOOING_DATA_DIR")
    if explicit_data:
        return Path(explicit_data).expanduser() / "attachments"
    project = _project_root()
    if project is not None:
        return project / "attachment"
    return _legacy_home_dir() / "attachments"


def init_shared_schema() -> Path:
    """앱 시작 시 1회. db / attachments dir 둘 다 보장 + P4 sync + 스키마
    init.

    CL #51107+: 새 위치 (`<project>/db/`) 진입 시 기존 home 위치
    (`~/.whooing/whooing-data.sqlite`) 의 db 가 있으면 *target 에 db 가
    없을 때만* 1회 복사 — 기존 사용자의 메모/해시태그 보존.

    CL #51119+ 사용자 요청: 시작 시 P4 환경이 갖춰져 있으면 `p4 sync` 로
    db 파일을 최신 (head) 으로 갱신 — 다른 환경에서 submit 된 변경분을
    받아온다. P4 부재면 silent skip. `core_db.init_schema` *이전* 에 sync
    해야 PRAGMA / schema_meta 쓰기가 새 head 위에 일어남.

    `WHOOING_DATA_DIR` 가 명시 set 되면 마이그레이션 + sync 모두 skip —
    테스트가 isolated tmp 에 실 사용자 데이터 / P4 상태를 끌어오지 않게.

    Returns: db 의 절대 경로 (편의).
    """
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if os.getenv("WHOOING_DATA_DIR") is None:
        _maybe_migrate_legacy_db(p.parent)
        # P4 환경 있을 때만 silent sync — 다음에 init_schema 가 idempotent
        # 한 PRAGMA / schema_meta 쓰기를 새 head 위에 적용.
        try:
            from whooing_tui import p4_sync
            p4_sync.sync_db_from_p4(p)
        except Exception:  # pragma: no cover — 절대 실패 표면화 X
            log.debug("p4 sync at startup failed (silent)", exc_info=True)
    attachments_root().mkdir(parents=True, exist_ok=True)
    core_db.init_schema(p)
    return p


def schema_version() -> int | None:
    """현재 db 의 schema_meta.version. db 없으면 None."""
    return core_db.current_version(db_path())


@contextmanager
def open_rw() -> Iterator[sqlite3.Connection]:
    """read-write 연결 — TUI 가 annotation / attachment / import_log write 할 때."""
    with core_db.open_rw(db_path()) as conn:
        yield conn


@contextmanager
def open_ro() -> Iterator[sqlite3.Connection]:
    """read-only 연결 — TUI 가 단순 조회 / 통계만 할 때 (mode=ro 안전)."""
    with core_db.open_ro(db_path()) as conn:
        yield conn
