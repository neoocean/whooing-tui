"""SQLite schema + 연결 helper — annotations / hashtags / entry_attachments /
statement_import_log.

소유 정책 (DESIGN §4):
  * **whooing-tui** 가 owner — `init_schema()` 로 스키마 생성/migrate.
  * **whooing-mcp-server-wrapper** 는 read-only — `open_ro()` 로 SELECT 만.
  * `pending` 테이블은 wrapper 잔류 (core 에 없음 — single consumer).

Schema version (CL #51155+ — review C1):
  v4 (CL #51100 baseline): 5 tables — entry_annotations / entry_hashtags /
       statement_import_log / entry_attachments / schema_meta.
  v5 (CL #51133): + entry_hashtags.section_id 컬럼 + idx_hashtags_section.
  v6 (CL #51147): + attachment_audit_log 테이블.
  v7 (CL #51151): + tag_meta 테이블.

`_apply_lightweight_migrations` 가 try/except 로 멱등 — 어떤 이전 버전에서
init 해도 v7 까지 자동 적용. wrapper / 다른 도구가 `current_version()` 으로
mismatch 감지하도록 SCHEMA_VERSION 도 동기 bump.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from whooing_core.dates import KST
from datetime import datetime

SCHEMA_VERSION = 8


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
        _apply_lightweight_migrations(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_lightweight_migrations(conn: sqlite3.Connection) -> None:
    """ALTER ADD COLUMN / CREATE TABLE IF NOT EXISTS 류의 멱등 마이그레이션."""
    # H2: entry_hashtags.section_id.
    try:
        conn.execute("ALTER TABLE entry_hashtags ADD COLUMN section_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            """UPDATE entry_hashtags
               SET section_id = (
                   SELECT section_id FROM entry_annotations
                   WHERE entry_annotations.entry_id = entry_hashtags.entry_id
               )
               WHERE section_id IS NULL"""
        )
    except sqlite3.OperationalError:  # pragma: no cover
        pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hashtags_section "
            "ON entry_hashtags(section_id)"
        )
    except sqlite3.OperationalError:  # pragma: no cover
        pass

    # CL #51151+ (H11): tag_meta — tag 별 메타 (color 등).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_meta (
            tag TEXT NOT NULL,
            section_id TEXT,                     -- NULL = 모든 섹션 default.
            color TEXT,                          -- Rich/Textual 색명 (예: 'red', 'cyan').
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tag, section_id)
        )
        """
    )

    # CL #51147+ (A16): attachment_audit_log.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS attachment_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            attachment_id INTEGER,        -- delete 후엔 row 사라져 NULL 가능.
            entry_id TEXT NOT NULL,
            action TEXT NOT NULL,         -- 'add' | 'delete' | 'note_edit' | 'restore'
            actor TEXT,                   -- 미래 다중 사용자 — 현재 NULL.
            ts TEXT NOT NULL,
            details_json TEXT             -- {filename, sha256, size, ...} JSON.
        )
        """
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attach_audit_attachment "
            "ON attachment_audit_log(attachment_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_attach_audit_entry "
            "ON attachment_audit_log(entry_id)"
        )
    except sqlite3.OperationalError:  # pragma: no cover
        pass

    # CL #52758+ (schema v8): entries_cache — 후잉 거래내역 영구 캐시.
    # 사용자 요청: 과거 데이터는 잘 변경 안 되므로 sqlite 캐시 + 점진적
    # 과거 윈도우 확장 + 컬럼 필터링이 캐시까지 검색하도록.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries_cache (
            section_id    TEXT NOT NULL,
            entry_id      TEXT NOT NULL,
            entry_date    TEXT NOT NULL,         -- YYYYMMDD or YYYYMMDD.NNNN
            l_account     TEXT,
            l_account_id  TEXT,
            r_account     TEXT,
            r_account_id  TEXT,
            money         INTEGER,
            item          TEXT,
            memo          TEXT,
            raw_json      TEXT,                  -- 원본 후잉 응답 (확장 필드 보존)
            fetched_at    TEXT NOT NULL,         -- ISO8601 KST
            PRIMARY KEY (section_id, entry_id)
        )
        """
    )
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_cache_date "
            "ON entries_cache(section_id, entry_date DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_cache_left "
            "ON entries_cache(section_id, l_account_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_cache_right "
            "ON entries_cache(section_id, r_account_id)"
        )
    except sqlite3.OperationalError:  # pragma: no cover
        pass


def log_attachment_audit(
    conn: sqlite3.Connection,
    *,
    attachment_id: int | None,
    entry_id: str,
    action: str,
    details: dict[str, Any] | None = None,
    actor: str | None = None,
) -> int:
    """attachment_audit_log 에 row 1개 insert. CL #51147+ (A16).

    `details` 는 JSON 으로 직렬화 — 호출자가 자유 dict (filename / sha /
    note before/after / trashed 여부 등). 실패 시도 raise X — silent log.
    """
    try:
        details_json = json.dumps(details or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        details_json = "{}"
    cur = conn.execute(
        """INSERT INTO attachment_audit_log
           (attachment_id, entry_id, action, actor, ts, details_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (attachment_id, entry_id, action, actor, _now_iso(), details_json),
    )
    return cur.lastrowid


def list_attachment_audit(
    conn: sqlite3.Connection,
    *,
    attachment_id: int | None = None,
    entry_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """audit log 조회. attachment_id / entry_id 둘 중 하나 이상 권장 —
    없으면 전체 (limit 적용). 결과는 시간 역순.
    """
    where: list[str] = []
    params: list[Any] = []
    if attachment_id is not None:
        where.append("attachment_id = ?")
        params.append(attachment_id)
    if entry_id is not None:
        where.append("entry_id = ?")
        params.append(entry_id)
    sql = "SELECT * FROM attachment_audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["details"] = json.loads(d.get("details_json") or "{}")
        except (TypeError, ValueError):
            d["details"] = {}
        out.append(d)
    return out


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
    *, section_id: str | None = None,
) -> list[str]:
    """전체 교체. 빈 list 면 모두 제거. annotation row 가 없으면 빈 메모로 생성.

    CL #51133+ (H2): `section_id` 매개변수 추가 — 명시되면 그 값으로,
    없으면 `entry_annotations.section_id` 에서 lookup. cross-section 통계
    오염을 막기 위해 새 row 도 section_id 보존.
    """
    if not conn.execute(
        "SELECT 1 FROM entry_annotations WHERE entry_id = ?", (entry_id,)
    ).fetchone():
        upsert_annotation(
            conn, entry_id=entry_id, section_id=section_id, note=None,
        )
    # section_id resolve — 명시값 우선, 없으면 annotation 에서.
    resolved_section = section_id
    if resolved_section is None:
        row = conn.execute(
            "SELECT section_id FROM entry_annotations WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if row:
            resolved_section = row["section_id"]
    conn.execute("DELETE FROM entry_hashtags WHERE entry_id = ?", (entry_id,))
    seen: set[str] = set()
    case_mode = _tag_case_mode()  # CL #51150+ (H4) — env 옵션.
    for raw in tags:
        # CL #51140+ (H3): 공백 포함 단일 토큰 보호.
        if raw is None:
            continue
        for t in str(raw).replace(",", " ").split():
            t = t.strip().lstrip("#")
            if not t:
                continue
            t = _apply_tag_case(t, case_mode)
            if t in seen:
                continue
            seen.add(t)
            conn.execute(
                "INSERT INTO entry_hashtags (entry_id, tag, section_id) "
                "VALUES (?, ?, ?)",
                (entry_id, t, resolved_section),
            )
    return sorted(seen)


def _tag_case_mode() -> str:
    """CL #51150+ (H4): `WHOOING_TAG_CASE_NORMALIZE` env 값 lookup.

      'preserve' (default) — 입력 그대로 (#Cafe / #cafe 별개).
      'lower' — 모든 영문을 소문자 (한글 영향 X — Hangul Syllables 는
        casefold 무관).
      'upper' — 영문을 대문자 (드물게 사용 — 정책 변경 안전망).

    잘못된 값은 'preserve' fallback.
    """
    import os
    raw = (os.getenv("WHOOING_TAG_CASE_NORMALIZE") or "").strip().lower()
    if raw in ("lower", "upper"):
        return raw
    return "preserve"


def _apply_tag_case(t: str, mode: str) -> str:
    if mode == "lower":
        return t.lower()
    if mode == "upper":
        return t.upper()
    return t


def list_hashtags(
    conn: sqlite3.Connection, *, section_id: str | None = None,
) -> dict[str, int]:
    """{tag: count}. section_id 가 명시되면 해당 섹션만 (CL #51133+ H2).

    None = 모든 섹션 합계 (종전 동작 — 후방 호환).
    """
    if section_id is None:
        rows = conn.execute(
            "SELECT tag, COUNT(*) AS n FROM entry_hashtags GROUP BY tag"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT tag, COUNT(*) AS n FROM entry_hashtags "
            "WHERE section_id = ? GROUP BY tag",
            (section_id,),
        ).fetchall()
    return {r["tag"]: r["n"] for r in rows}


def find_entries_by_hashtag(
    conn: sqlite3.Connection, tag: str,
    *, section_id: str | None = None,
) -> list[str]:
    """해당 tag 가 붙은 entry_id list. section_id 명시 시 그 섹션만."""
    if section_id is None:
        rows = conn.execute(
            "SELECT entry_id FROM entry_hashtags WHERE tag = ? ORDER BY entry_id",
            (tag,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT entry_id FROM entry_hashtags "
            "WHERE tag = ? AND section_id = ? ORDER BY entry_id",
            (tag, section_id),
        ).fetchall()
    return [r["entry_id"] for r in rows]


def set_tag_color(
    conn: sqlite3.Connection,
    tag: str,
    color: str | None,
    *,
    section_id: str | None = None,
) -> None:
    """tag 의 색 지정/제거. CL #51151+ (H11).

    `color=None` → row 삭제 (기본 색으로 fallback). 같은 (tag, section_id)
    PRIMARY KEY 라 INSERT OR REPLACE.
    """
    tag = tag.strip().lstrip("#")
    if not tag:
        return
    if color is None or not color.strip():
        if section_id is None:
            conn.execute(
                "DELETE FROM tag_meta WHERE tag = ? AND section_id IS NULL",
                (tag,),
            )
        else:
            conn.execute(
                "DELETE FROM tag_meta WHERE tag = ? AND section_id = ?",
                (tag, section_id),
            )
        return
    conn.execute(
        "INSERT OR REPLACE INTO tag_meta (tag, section_id, color, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (tag, section_id, color.strip(), _now_iso()),
    )


def get_tag_colors(
    conn: sqlite3.Connection, *, section_id: str | None = None,
) -> dict[str, str]:
    """{tag: color} — 섹션별 + NULL (default) 합쳐서. 섹션 명시값이 default 이김.

    CL #51151+ (H11).
    """
    out: dict[str, str] = {}
    # default (section_id IS NULL) 부터.
    for r in conn.execute(
        "SELECT tag, color FROM tag_meta WHERE section_id IS NULL"
    ).fetchall():
        if r["color"]:
            out[r["tag"]] = r["color"]
    # 섹션 명시 — overwrite.
    if section_id is not None:
        for r in conn.execute(
            "SELECT tag, color FROM tag_meta WHERE section_id = ?",
            (section_id,),
        ).fetchall():
            if r["color"]:
                out[r["tag"]] = r["color"]
    return out


def add_tag_to_entries(
    conn: sqlite3.Connection, entry_ids: list[str], tag: str,
    *, section_id: str | None = None,
) -> int:
    """여러 entry 에 같은 tag 일괄 추가. CL #51145+ (H6).

    각 entry 에:
      - entry_annotations row 가 없으면 빈 메모로 생성 (FK 위해).
      - 같은 (entry_id, tag) 가 이미 있으면 skip (PRIMARY KEY).

    Returns: 새로 추가된 row 수 (이미 있던 건 제외).
    """
    tag = tag.strip().lstrip("#")
    if not tag or not entry_ids:
        return 0
    added = 0
    for eid in entry_ids:
        # annotation row 보장.
        if not conn.execute(
            "SELECT 1 FROM entry_annotations WHERE entry_id = ?", (eid,)
        ).fetchone():
            upsert_annotation(
                conn, entry_id=eid, section_id=section_id, note=None,
            )
        # section_id resolve (명시 없으면 annotation 에서).
        resolved_section = section_id
        if resolved_section is None:
            row = conn.execute(
                "SELECT section_id FROM entry_annotations WHERE entry_id = ?",
                (eid,),
            ).fetchone()
            if row:
                resolved_section = row["section_id"]
        # 이미 있으면 skip.
        existing = conn.execute(
            "SELECT 1 FROM entry_hashtags WHERE entry_id = ? AND tag = ?",
            (eid, tag),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO entry_hashtags (entry_id, tag, section_id) "
            "VALUES (?, ?, ?)",
            (eid, tag, resolved_section),
        )
        added += 1
    return added


def remove_tag_from_entries(
    conn: sqlite3.Connection, entry_ids: list[str], tag: str,
    *, section_id: str | None = None,
) -> int:
    """여러 entry 에서 같은 tag 일괄 제거. CL #51145+ (H6).

    Returns: 제거된 row 수.
    """
    tag = tag.strip().lstrip("#")
    if not tag or not entry_ids:
        return 0
    placeholders = ",".join("?" * len(entry_ids))
    if section_id is None:
        cur = conn.execute(
            f"DELETE FROM entry_hashtags WHERE tag = ? "
            f"AND entry_id IN ({placeholders})",
            [tag, *entry_ids],
        )
    else:
        cur = conn.execute(
            f"DELETE FROM entry_hashtags WHERE tag = ? AND section_id = ? "
            f"AND entry_id IN ({placeholders})",
            [tag, section_id, *entry_ids],
        )
    return cur.rowcount or 0


def rename_tag(
    conn: sqlite3.Connection, old: str, new: str,
    *, section_id: str | None = None,
) -> dict[str, Any]:
    """모든 entry 의 `old` 태그를 `new` 로 갱신.

    CL #51135+ (H5): 오타 수정 / 명명 통일 도구. 같은 entry 가 이미 `new` 도
    가지고 있으면 dedup (PRIMARY KEY 충돌 회피 — 먼저 새 row 가 있는지
    검사해 skip).

    `section_id` 명시 시 해당 섹션의 row 만 영향.

    Returns:
      {'renamed': N, 'merged_into_existing': M} —
      renamed = old → new 로 직접 변경된 row,
      merged = new 가 이미 있어 old 만 삭제된 row.
    """
    old = old.strip().lstrip("#")
    new = new.strip().lstrip("#")
    if not old or not new:
        return {"renamed": 0, "merged_into_existing": 0}
    if old == new:
        return {"renamed": 0, "merged_into_existing": 0}

    # 1. new 를 이미 가진 entry 들 → old 만 delete (merge).
    if section_id is None:
        merged_rows = conn.execute(
            """SELECT entry_id FROM entry_hashtags
               WHERE tag = ? AND entry_id IN (
                   SELECT entry_id FROM entry_hashtags WHERE tag = ?
               )""",
            (old, new),
        ).fetchall()
    else:
        merged_rows = conn.execute(
            """SELECT entry_id FROM entry_hashtags
               WHERE tag = ? AND section_id = ? AND entry_id IN (
                   SELECT entry_id FROM entry_hashtags
                   WHERE tag = ? AND section_id = ?
               )""",
            (old, section_id, new, section_id),
        ).fetchall()
    merged_count = len(merged_rows)
    if merged_count:
        if section_id is None:
            conn.executemany(
                "DELETE FROM entry_hashtags WHERE entry_id = ? AND tag = ?",
                [(r["entry_id"], old) for r in merged_rows],
            )
        else:
            conn.executemany(
                "DELETE FROM entry_hashtags "
                "WHERE entry_id = ? AND tag = ? AND section_id = ?",
                [(r["entry_id"], old, section_id) for r in merged_rows],
            )

    # 2. 나머지 — old 를 new 로 UPDATE.
    if section_id is None:
        cur = conn.execute(
            "UPDATE entry_hashtags SET tag = ? WHERE tag = ?",
            (new, old),
        )
    else:
        cur = conn.execute(
            "UPDATE entry_hashtags SET tag = ? "
            "WHERE tag = ? AND section_id = ?",
            (new, old, section_id),
        )
    return {"renamed": cur.rowcount or 0, "merged_into_existing": merged_count}


def merge_tags(
    conn: sqlite3.Connection, sources: list[str], dest: str,
    *, section_id: str | None = None,
) -> dict[str, int]:
    """여러 source 태그를 dest 로 통합. CL #51135+ (H5).

    각 source 마다 `rename_tag(source, dest)` — dest 가 이미 있는 entry 의
    경우 자동 dedup. dest 자체는 source list 에서 자동 제외.

    Returns:
      {source_tag: rename_result_dict, ...}
    """
    out: dict[str, dict[str, int]] = {}
    for src in sources:
        if src == dest:
            continue
        r = rename_tag(conn, src, dest, section_id=section_id)
        out[src] = r
    # 호환 위해 카운트 합계도 같은 dict 안에 보관.
    return {
        "sources_processed": len(out),
        "rows_renamed": sum(r["renamed"] for r in out.values()),
        "rows_merged": sum(r["merged_into_existing"] for r in out.values()),
    }


def delete_tag(
    conn: sqlite3.Connection, tag: str,
    *, section_id: str | None = None,
) -> int:
    """모든 (또는 한 섹션의) entry 에서 해당 tag 제거. CL #51135+ (H5).

    Returns: 삭제된 row 수.
    """
    tag = tag.strip().lstrip("#")
    if not tag:
        return 0
    if section_id is None:
        cur = conn.execute(
            "DELETE FROM entry_hashtags WHERE tag = ?", (tag,),
        )
    else:
        cur = conn.execute(
            "DELETE FROM entry_hashtags WHERE tag = ? AND section_id = ?",
            (tag, section_id),
        )
    return cur.rowcount or 0


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


def find_imports_by_natural_key(
    conn: sqlite3.Connection,
    *,
    entry_date: str,
    total_amount: int,
    merchant: str,
    section_id: str | None = None,
    statuses: tuple[str, ...] = ("inserted", "matched_existing"),
) -> list[dict[str, Any]]:
    """statement_import_log 에서 같은 자연키 (date+amount+merchant) 의 이전
    import 기록 검색.

    CL #51129+ — 같은 명세서를 두 번 import 하거나 두 명세서가 동일 거래를
    중복 보고할 때 dedup 강화 용도. row 의 schema 변경 없이 자연키로 매칭.

    Args:
      entry_date: YYYYMMDD 8자리.
      total_amount: 정수 (KRW). statement_import_log 의 `total_amount` 컬럼.
      merchant: 가맹점명. CSV/HTML/PDF 어댑터마다 strip/normalize 정도가
        달라서 쿼리는 양쪽 모두 strip 비교 (collation X — exact match 만 —
        정확도 높이기 위해 caller 가 같은 normalize 적용 권장).
      section_id: None 이면 모든 섹션. 명시되면 해당 섹션만.
      statuses: 매칭으로 인정할 status 들. default = 실제 입력된 것
        ('inserted') + 기존 거래로 매칭된 것 ('matched_existing'). 'failed'
        / 'dry_run' 은 제외 — 재시도 가치 있는 상태.

    Returns:
      매칭 row 들의 dict list (시간순). 빈 list 면 미매칭.
    """
    if not statuses:
        return []
    placeholders = ",".join("?" * len(statuses))
    sql = (
        "SELECT * FROM statement_import_log "
        f"WHERE entry_date = ? AND total_amount = ? AND merchant = ? "
        f"AND status IN ({placeholders})"
    )
    params: list[Any] = [entry_date, int(total_amount), merchant, *statuses]
    if section_id is not None:
        sql += " AND section_id = ?"
        params.append(section_id)
    sql += " ORDER BY imported_at"
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


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
