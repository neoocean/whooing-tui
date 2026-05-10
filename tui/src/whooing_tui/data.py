"""TUI 의 데이터 layer — whooing-core 를 import 해 SQLite + 첨부 storage 관리.

TUI 가 db + attachments 의 owner — schema 생성/마이그레이션 책임. 본래
wrapper (whooing-mcp-server-wrapper) 가 같은 db 를 read-only 로 SELECT
하도록 설계됐으나 **wrapper 는 2026-05-10 종료 (archived)** — 현재 SELECT
경로의 외부 사용자는 없다. `open_ro()` API 는 안전성 / 명분 분리를 위해
그대로 유지 (다른 도구가 미래에 합류할 가능성).

Path 정책 (v0.1.0+, monorepo):
  $WHOOING_DATA_DIR > ~/.whooing/   (default)
   └─ whooing-data.sqlite            sqlite db (annotations / hashtags /
                                     entry_attachments / statement_import_log)
   └─ attachments/YYYY/YYYY-MM-DD/   sha256 dedup file storage
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import sqlite3

from whooing_core import db as core_db


def data_dir() -> Path:
    """공유 데이터 root. WHOOING_DATA_DIR > ~/.whooing 기본."""
    explicit = os.getenv("WHOOING_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    return Path("~/.whooing").expanduser()


def db_path() -> Path:
    """SQLite db 의 절대 경로."""
    return data_dir() / "whooing-data.sqlite"


def attachments_root() -> Path:
    """첨부 storage root (TUI write, wrapper read-only)."""
    return data_dir() / "attachments"


def init_shared_schema() -> Path:
    """앱 시작 시 1회. db / attachments dir 둘 다 보장 + 스키마 init.

    Returns: db 의 절대 경로 (편의).
    """
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
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
