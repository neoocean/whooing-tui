"""SQLite schema + 연결 helper — annotations / hashtags / entry_attachments /
statement_import_log.

소유 정책 (DESIGN §4):
  * **whooing-tui** 가 owner — `init_schema()` 로 스키마 생성/migrate.
  * **whooing-mcp-server-wrapper** 는 read-only — `open_ro()` 로 SELECT 만.
  * `pending` 테이블은 wrapper 잔류 (core 에 없음 — single consumer).

Schema version: 4 (extracted from wrapper v0.1.12).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from whooing_core.dates import KST
from datetime import datetime

SCHEMA_VERSION = 4


# ---- 연결 + schema -----------------------------------------------------


def init_schema(db_path: str | Path) -> None:
    """TUI 가 처음/마이그레이션 시 호출. WAL 모드 + busy_timeout 설정.

    멱등 — 이미 최신이면 noop. wrapper 가 `current_version()` 으로 mismatch 를
    감지하면 사용자에게 "TUI 를 먼저 실행하세요" 안내.
    """
    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        _create_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    finally:
        conn.close()


def current_version(db_path: str | Path) -> int | None:
    """schema_meta.version 반환. db 없거나 schema_meta 없으면 None."""
    p = Path(db_path).expanduser()
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        try:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'version'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None
    finally:
        conn.close()


@contextmanager
def open_rw(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """TUI 용 — 모든 권한. busy_timeout/foreign_keys 자동 설정."""
    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def open_ro(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """wrapper 용 — 같은 db 를 SELECT-only 로 연다.

    `mode=ro` URI 가 OS-level 보장 (writes 가 ResultError 로 fail). WAL 모드의
    동시 SELECT 는 TUI write 와 충돌 없음.
    """
    p = Path(db_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(
            f"db 가 없음: {p}. TUI (whooing-tui) 를 먼저 실행하세요."
        )
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---- DDL ---------------------------------------------------------------


def _create_tables(conn: sqlite3.Connection) -> None:
    """v4 schema (CREATE IF NOT EXISTS — 마이그레이션 친화)."""
    conn.executescript(
        """
        -- entry annotations (메모) — TUI 가 write
        CREATE TABLE IF NOT EXISTS entry_annotations (
            entry_id TEXT PRIMARY KEY,
            section_id TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- entry hashtags (1:N) — TUI 가 write
        CREATE TABLE IF NOT EXISTS entry_hashtags (
            entry_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (entry_id, tag),
            FOREIGN KEY (entry_id) REFERENCES entry_annotations(entry_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_hashtags_tag ON entry_hashtags(tag);

        -- statement import 추적
        CREATE TABLE IF NOT EXISTS statement_import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            source_kind TEXT NOT NULL,         -- 'pdf' | 'csv' | 'html'
            statement_period_start TEXT,
            statement_period_end TEXT,
            issuer TEXT,
            card_label TEXT,
            entry_date TEXT NOT NULL,
            merchant TEXT NOT NULL,
            original_amount INTEGER NOT NULL,
            fee_amount INTEGER NOT NULL DEFAULT 0,
            total_amount INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'KRW',
            foreign_amount REAL,
            exchange_rate REAL,
            section_id TEXT NOT NULL,
            l_account_id TEXT NOT NULL,
            r_account_id TEXT NOT NULL,
            whooing_entry_id TEXT,
            status TEXT NOT NULL,              -- 'inserted' | 'failed' | 'dry_run' | 'matched_existing'
            imported_at TEXT NOT NULL,
            error_message TEXT,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_import_source ON statement_import_log(source_file);
        CREATE INDEX IF NOT EXISTS idx_import_entry_date ON statement_import_log(entry_date);
        CREATE INDEX IF NOT EXISTS idx_import_whooing_entry ON statement_import_log(whooing_entry_id);

        -- 거래 ↔ 첨부파일 (1:N). 파일 본체는 attachments_root/YYYY/YYYY-MM-DD/
        -- 같은 sha256 은 dedup (디스크에 한 번만).
        CREATE TABLE IF NOT EXISTS entry_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            section_id TEXT,
            file_path TEXT NOT NULL,           -- relative to attachments_root
            original_path TEXT,
            original_filename TEXT NOT NULL,
            file_size_bytes INTEGER,
            file_sha256 TEXT,
            mime_type TEXT,
            note TEXT,
            attached_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attach_entry_id ON entry_attachments(entry_id);
        CREATE INDEX IF NOT EXISTS idx_attach_sha256 ON entry_attachments(file_sha256);

        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


# ---- helpers (datetime) ------------------------------------------------


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


# ---- annotations CRUD --------------------------------------------------


def upsert_annotation(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    section_id: str | None,
    note: str | None,
) -> dict[str, Any]:
    """memo 신규/갱신. note=None 도 허용 (해시태그만 변경하고 싶을 때)."""
    now = _now_iso()
    existing = conn.execute(
        "SELECT * FROM entry_annotations WHERE entry_id = ?", (entry_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE entry_annotations
               SET note = COALESCE(?, note),
                   section_id = COALESCE(?, section_id),
                   updated_at = ?
               WHERE entry_id = ?""",
            (note, section_id, now, entry_id),
        )
    else:
        conn.execute(
            """INSERT INTO entry_annotations
               (entry_id, section_id, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (entry_id, section_id, note, now, now),
        )
    row = conn.execute(
        "SELECT * FROM entry_annotations WHERE entry_id = ?", (entry_id,)
    ).fetchone()
    return dict(row)


def remove_annotation(conn: sqlite3.Connection, entry_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM entry_annotations WHERE entry_id = ?", (entry_id,)
    ).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM entry_annotations WHERE entry_id = ?", (entry_id,))
    return dict(row)


def set_hashtags(
    conn: sqlite3.Connection, entry_id: str, tags: list[str],
) -> list[str]:
    """전체 교체. 빈 list 면 모두 제거. annotation row 가 없으면 빈 메모로 생성."""
    # ensure annotation row (FK 위해)
    if not conn.execute(
        "SELECT 1 FROM entry_annotations WHERE entry_id = ?", (entry_id,)
    ).fetchone():
        upsert_annotation(conn, entry_id=entry_id, section_id=None, note=None)
    conn.execute("DELETE FROM entry_hashtags WHERE entry_id = ?", (entry_id,))
    seen: set[str] = set()
    for t in tags:
        t = t.strip().lstrip("#")
        if not t or t in seen:
            continue
        seen.add(t)
        conn.execute(
            "INSERT INTO entry_hashtags (entry_id, tag) VALUES (?, ?)",
            (entry_id, t),
        )
    return sorted(seen)


def list_hashtags(conn: sqlite3.Connection) -> dict[str, int]:
    """{tag: count} — 사용 빈도 내림차순 정렬은 caller 가."""
    rows = conn.execute(
        "SELECT tag, COUNT(*) AS n FROM entry_hashtags GROUP BY tag"
    ).fetchall()
    return {r["tag"]: r["n"] for r in rows}


def find_entries_by_hashtag(
    conn: sqlite3.Connection, tag: str,
) -> list[str]:
    """해당 tag 가 붙은 entry_id list."""
    rows = conn.execute(
        "SELECT entry_id FROM entry_hashtags WHERE tag = ? ORDER BY entry_id",
        (tag,),
    ).fetchall()
    return [r["entry_id"] for r in rows]


def get_annotations_for(
    conn: sqlite3.Connection, entry_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """{entry_id: {note, hashtags: [...]}} — 한 query 로 batch."""
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    notes_rows = conn.execute(
        f"SELECT * FROM entry_annotations WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall()
    tag_rows = conn.execute(
        f"SELECT entry_id, tag FROM entry_hashtags WHERE entry_id IN ({placeholders})",
        entry_ids,
    ).fetchall()
    out: dict[str, dict[str, Any]] = {
        r["entry_id"]: dict(r) for r in notes_rows
    }
    for r in tag_rows:
        out.setdefault(r["entry_id"], {"entry_id": r["entry_id"]})\
           .setdefault("hashtags", []).append(r["tag"])
    return out


# ---- import_log helpers -----------------------------------------------


def log_import(
    conn: sqlite3.Connection,
    *,
    source_file: str,
    source_kind: str,                   # 'pdf' | 'csv' | 'html'
    statement_period_start: str | None,
    statement_period_end: str | None,
    issuer: str | None,
    card_label: str | None,
    entry_date: str,
    merchant: str,
    original_amount: int,
    fee_amount: int,
    total_amount: int,
    currency: str,
    foreign_amount: float | None,
    exchange_rate: float | None,
    section_id: str,
    l_account_id: str,
    r_account_id: str,
    whooing_entry_id: str | None,
    status: str,                        # 'inserted' | 'failed' | 'dry_run' | 'matched_existing'
    error_message: str | None,
    notes: str | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO statement_import_log
           (source_file, source_kind, statement_period_start, statement_period_end,
            issuer, card_label, entry_date, merchant,
            original_amount, fee_amount, total_amount,
            currency, foreign_amount, exchange_rate,
            section_id, l_account_id, r_account_id,
            whooing_entry_id, status, imported_at, error_message, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_file, source_kind, statement_period_start, statement_period_end,
         issuer, card_label, entry_date, merchant,
         original_amount, fee_amount, total_amount,
         currency, foreign_amount, exchange_rate,
         section_id, l_account_id, r_account_id,
         whooing_entry_id, status, _now_iso(), error_message, notes),
    )
    return cur.lastrowid
