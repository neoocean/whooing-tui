"""거래 수정 이력 + 소프트삭제(안 B) + 복원/되돌리기 — sqlite 레이어.

설계는 [`docs/scenarios/11-edit-history-and-soft-delete.md`](../../../docs/scenarios/11-edit-history-and-soft-delete.md).

핵심 개념
---------
- **logical_id**: 한 거래의 불변 anchor. `create` 시점의 후잉 `entry_id`.
  안 B 에서는 삭제 시 후잉 entry 를 실제로 지우고 복원 시 재생성하므로
  후잉 `entry_id` 가 바뀐다 — 그래도 `logical_id` 로 동일성을 잇는다.
- **entry_revisions**: append-only 버전 로그. 매 수정/삭제/복원/되돌리기마다
  거래 전체 스냅샷 1행. 영구삭제(`purge_logical`) 외엔 UPDATE/DELETE 안 함.
- **entry_head**: `logical_id` 당 1행 — 현재(head) 상태 비정규화 캐시
  (현재 후잉 id, 삭제 여부, head revision_no). 목록/휴지통 O(1) 조회용.

본 모듈은 `whooing_core.entries_cache` 와 같은 패턴 — 순수 헬퍼 +
`sqlite3.Connection` 을 받는 함수. 후잉 REST 호출은 *하지 않는다*
(호출자/TUI repository 가 후잉 mutation 과 본 기록을 묶는다).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable

from whooing_core.dates import KST

# op 종류 -----------------------------------------------------------------
OP_CREATE = "create"
OP_EDIT = "edit"
OP_DELETE = "delete"
OP_RESTORE = "restore"
OP_REVERT = "revert"
OP_EXTERNAL = "external"
VALID_OPS = frozenset(
    {OP_CREATE, OP_EDIT, OP_DELETE, OP_RESTORE, OP_REVERT, OP_EXTERNAL}
)

# 거래 본문 스냅샷 필드 (후잉 entries 응답 키 그대로).
SNAPSHOT_FIELDS = (
    "entry_date",
    "l_account",
    "l_account_id",
    "r_account",
    "r_account_id",
    "money",
    "item",
    "memo",
)


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def _coerce_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


# ---- 순수 헬퍼 (sqlite 무관) --------------------------------------------


def snapshot_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """후잉 entry dict → 스냅샷 dict (SNAPSHOT_FIELDS 만, money int 강제)."""
    snap: dict[str, Any] = {}
    for f in SNAPSHOT_FIELDS:
        snap[f] = entry.get(f)
    snap["money"] = _coerce_int(entry.get("money"))
    return snap


def diff(
    prev: dict[str, Any] | None, cur: dict[str, Any],
) -> list[tuple[str, Any, Any]]:
    """두 스냅샷의 차이 — `(field, old, new)` list. prev None 이면 전체가 신규."""
    out: list[tuple[str, Any, Any]] = []
    for f in SNAPSHOT_FIELDS:
        old = (prev or {}).get(f)
        new = cur.get(f)
        if old != new:
            out.append((f, old, new))
    return out


def summarize_diff(changes: list[tuple[str, Any, Any]]) -> str:
    """diff() 결과를 한 줄 요약. 빈 변경이면 '(변경 없음)'."""
    if not changes:
        return "(변경 없음)"
    parts: list[str] = []
    for field, old, new in changes:
        if field == "money":
            old_s = f"{old:,}" if isinstance(old, int) else str(old)
            new_s = f"{new:,}" if isinstance(new, int) else str(new)
            parts.append(f"money {old_s}→{new_s}")
        else:
            parts.append(f"{field} {old!r}→{new!r}")
    return ", ".join(parts)


# ---- sqlite 레이어 ------------------------------------------------------


def _next_revision_no(conn: sqlite3.Connection, logical_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(revision_no), 0) + 1 AS n "
        "FROM entry_revisions WHERE logical_id = ?",
        (logical_id,),
    ).fetchone()
    return int(row[0]) if row else 1


def _upsert_head(
    conn: sqlite3.Connection,
    *,
    logical_id: str,
    section_id: str | None,
    current_entry_id: str | None,
    head_revision_no: int,
    is_deleted: bool,
    updated_at: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO entry_head
           (logical_id, section_id, current_entry_id, head_revision_no,
            is_deleted, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            logical_id,
            section_id,
            current_entry_id,
            head_revision_no,
            1 if is_deleted else 0,
            updated_at,
        ),
    )


def record_revision(
    conn: sqlite3.Connection,
    *,
    logical_id: str,
    whooing_entry_id: str | None,
    section_id: str | None,
    op: str,
    snapshot: dict[str, Any],
    is_deleted: bool = False,
    created_at: str | None = None,
    source: str = "tui",
    reverted_from: int | None = None,
    note: str | None = None,
) -> int:
    """버전 1행 append + entry_head 갱신. 반환 = 새 revision_no.

    `is_deleted=True` (op=delete) 면 head.current_entry_id 는 NULL 로 둔다
    (안 B: 후잉에서 실삭제됨). 복원/되돌리기는 새 whooing_entry_id 와 함께
    `is_deleted=False` 로 호출.
    """
    if op not in VALID_OPS:
        raise ValueError(f"unknown op: {op!r}")
    if not logical_id:
        raise ValueError("logical_id 는 필수")
    ts = created_at or _now_iso()
    rev_no = _next_revision_no(conn, logical_id)
    snap = snapshot_fields(snapshot)
    conn.execute(
        """INSERT INTO entry_revisions
           (logical_id, revision_no, whooing_entry_id, section_id, op,
            entry_date, l_account, l_account_id, r_account, r_account_id,
            money, item, memo, is_deleted, created_at, source,
            reverted_from, note)
           VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?)""",
        (
            logical_id, rev_no, whooing_entry_id, section_id, op,
            snap["entry_date"], snap["l_account"], snap["l_account_id"],
            snap["r_account"], snap["r_account_id"], snap["money"],
            snap["item"], snap["memo"], 1 if is_deleted else 0, ts, source,
            reverted_from, note,
        ),
    )
    _upsert_head(
        conn,
        logical_id=logical_id,
        section_id=section_id,
        current_entry_id=None if is_deleted else whooing_entry_id,
        head_revision_no=rev_no,
        is_deleted=is_deleted,
        updated_at=ts,
    )
    return rev_no


def ensure_baseline(
    conn: sqlite3.Connection,
    *,
    entry: dict[str, Any],
    section_id: str | None,
    source: str = "tui",
    created_at: str | None = None,
) -> str:
    """이 후잉 entry 가 아직 추적 안 되면 `op=create` baseline 1버전 lazy seed.

    이미 추적 중이면(entry_head 매핑 존재) 기존 logical_id 반환. 기존 거래는
    첫 수정/삭제 시점에 이걸로 현재값 baseline 을 깔고 그때부터 추적한다
    (전체 일괄 seed 비용 회피 — docs/scenarios/11 "마이그레이션").

    반환 = logical_id.
    """
    eid = str(entry.get("entry_id") or "")
    if not eid:
        raise ValueError("entry 에 entry_id 필요")
    existing = logical_id_for_entry(conn, eid)
    if existing:
        return existing
    record_revision(
        conn,
        logical_id=eid,
        whooing_entry_id=eid,
        section_id=section_id,
        op=OP_CREATE,
        snapshot=snapshot_fields(entry),
        is_deleted=False,
        created_at=created_at,
        source=source,
    )
    return eid


def head_for(conn: sqlite3.Connection, logical_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM entry_head WHERE logical_id = ?", (logical_id,)
    ).fetchone()
    return dict(row) if row else None


def logical_id_for_entry(
    conn: sqlite3.Connection, whooing_entry_id: str,
) -> str | None:
    """현재 후잉 entry_id → logical_id. 살아있는 매핑(entry_head) 우선,
    없으면 과거 revision 에서 역추적.
    """
    if not whooing_entry_id:
        return None
    row = conn.execute(
        "SELECT logical_id FROM entry_head WHERE current_entry_id = ? LIMIT 1",
        (str(whooing_entry_id),),
    ).fetchone()
    if row:
        return row[0]
    row = conn.execute(
        "SELECT logical_id FROM entry_revisions WHERE whooing_entry_id = ? "
        "ORDER BY revision_no DESC LIMIT 1",
        (str(whooing_entry_id),),
    ).fetchone()
    return row[0] if row else None


def list_revisions(
    conn: sqlite3.Connection, logical_id: str,
) -> list[dict[str, Any]]:
    """logical_id 의 전체 버전 — revision_no 오름차순 (오래된→최신)."""
    rows = conn.execute(
        "SELECT * FROM entry_revisions WHERE logical_id = ? "
        "ORDER BY revision_no ASC",
        (logical_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_revision(
    conn: sqlite3.Connection, logical_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM entry_revisions WHERE logical_id = ? "
        "ORDER BY revision_no DESC LIMIT 1",
        (logical_id,),
    ).fetchone()
    return dict(row) if row else None


def list_deleted(
    conn: sqlite3.Connection, section_id: str | None = None,
) -> list[dict[str, Any]]:
    """삭제 마킹(휴지통) 상태인 logical entry 목록 — head + 마지막 스냅샷.

    각 dict 는 entry_head 컬럼 + 마지막 revision 의 본문 필드(표시용).
    최근 삭제 순 (updated_at desc).
    """
    if section_id is not None:
        heads = conn.execute(
            "SELECT * FROM entry_head WHERE is_deleted = 1 AND section_id = ? "
            "ORDER BY updated_at DESC",
            (section_id,),
        ).fetchall()
    else:
        heads = conn.execute(
            "SELECT * FROM entry_head WHERE is_deleted = 1 "
            "ORDER BY updated_at DESC"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for h in heads:
        d = dict(h)
        last = latest_revision(conn, d["logical_id"])
        if last:
            for f in SNAPSHOT_FIELDS:
                d[f] = last.get(f)
            d["deleted_at"] = last.get("created_at")
        out.append(d)
    return out


def purge_logical(conn: sqlite3.Connection, logical_id: str) -> int:
    """영구삭제 — 한 logical entry 의 모든 버전 + head 제거. 반환 = 삭제 행수.

    유일한 파괴적 동작. 휴지통에서 '영구삭제' 확정 시에만 호출.
    """
    cur = conn.execute(
        "DELETE FROM entry_revisions WHERE logical_id = ?", (logical_id,)
    )
    conn.execute("DELETE FROM entry_head WHERE logical_id = ?", (logical_id,))
    return cur.rowcount or 0


def deleted_entry_ids(
    conn: sqlite3.Connection, section_id: str | None = None,
) -> set[str]:
    """현재 삭제 마킹된 logical 들이 *마지막에 가졌던* 후잉 entry_id 집합.

    안 B 에선 삭제 시 후잉에서 실제로 사라지므로 보통 entries_cache 와
    겹치지 않지만, 캐시가 아직 안 비워졌을 때 목록에서 숨기기 위한 안전망.
    """
    rows = (
        conn.execute(
            "SELECT r.whooing_entry_id FROM entry_head h "
            "JOIN entry_revisions r ON r.logical_id = h.logical_id "
            "WHERE h.is_deleted = 1 AND r.whooing_entry_id IS NOT NULL"
            + (" AND h.section_id = ?" if section_id is not None else ""),
            (section_id,) if section_id is not None else (),
        ).fetchall()
    )
    return {str(r[0]) for r in rows if r[0]}


__all__ = [
    "OP_CREATE", "OP_EDIT", "OP_DELETE", "OP_RESTORE", "OP_REVERT",
    "OP_EXTERNAL", "VALID_OPS", "SNAPSHOT_FIELDS",
    "snapshot_fields", "diff", "summarize_diff",
    "record_revision", "ensure_baseline", "head_for", "logical_id_for_entry",
    "list_revisions", "latest_revision", "list_deleted",
    "purge_logical", "deleted_entry_ids",
]
