"""후잉 거래내역 영구 sqlite 캐시 — entries_cache 테이블 (schema v8).

CL #52758+. 사용자 요청:
> 과거의 데이터는 잘 변경되지 않기 때문에 sqlite 파일에 캐시했다가
> 나중에 유사한 요청을 하면 먼저 캐시를 보여주고 변경된 부분만
> 조회해 업데이트하고 다시 캐시하도록 수정해주세요.

배경:
- 후잉 list_entries 가 최대 1년 윈도우 / 100건 cap. 사용자 가계부의
  과거 12개월 분량을 한 번에 가져오면 수십 호출 + 분당 20회 throttle 에
  걸려 느리다.
- 컬럼 필터 (예: 같은 left=식비) 가 의미 있으려면 최근 1개월보다 더
  넓은 범위 검색 필요. 캐시 hit 으로 즉시 결과 + background 로 더 확장.

본 모듈은 thin sqlite layer — pure 함수 (transaction 은 caller).
호출자 (TUI 의 EntriesScreen worker) 가 fetch → upsert → list 흐름.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, Iterable

from whooing_core.dates import KST

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def _coerce_int(v: Any) -> int | None:
    """money 같은 정수 필드 — 후잉 응답이 str / float 일 수도 있어 방어적."""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def upsert_entries(
    conn: sqlite3.Connection,
    section_id: str,
    entries: Iterable[dict[str, Any]],
) -> int:
    """후잉 응답 entries 를 캐시에 INSERT OR REPLACE.

    반환: 처리한 row 수 (실패 X).

    raw_json 에 원본 dict 를 보존 — 추후 새 필드 활용 시 schema 변경 없이
    역추적 가능.
    """
    rows: list[tuple] = []
    now = _now_iso()
    for e in entries:
        eid = e.get("entry_id")
        if eid is None:
            continue
        eid = str(eid)
        rows.append((
            section_id,
            eid,
            str(e.get("entry_date") or ""),
            (e.get("l_account") or None),
            (e.get("l_account_id") or None),
            (e.get("r_account") or None),
            (e.get("r_account_id") or None),
            _coerce_int(e.get("money")),
            (e.get("item") or None),
            (e.get("memo") or None),
            json.dumps(e, ensure_ascii=False, default=str),
            now,
        ))
    if not rows:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO entries_cache
           (section_id, entry_id, entry_date,
            l_account, l_account_id, r_account, r_account_id,
            money, item, memo, raw_json, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    """sqlite Row → 후잉 응답 모양 dict.

    raw_json 우선 (확장 필드 보존), 단 정규화한 컬럼 값 (특히 money 의 int
    cast) 을 그 위에 덮어서 caller 가 일관된 타입을 받게 한다.
    """
    raw = row["raw_json"]
    d: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                d = parsed
        except (ValueError, TypeError):
            pass
    # 정규화된 컬럼 값을 위로 (raw 의 str/float money 같은 inconsistency 차단).
    d.setdefault("entry_id", row["entry_id"])
    d.setdefault("entry_date", row["entry_date"])
    d.setdefault("l_account", row["l_account"])
    d.setdefault("l_account_id", row["l_account_id"])
    d.setdefault("r_account", row["r_account"])
    d.setdefault("r_account_id", row["r_account_id"])
    d.setdefault("item", row["item"])
    d.setdefault("memo", row["memo"])
    # money 만은 컬럼 값으로 강제 override (int 보장).
    if row["money"] is not None:
        d["money"] = row["money"]
    d["_cache_fetched_at"] = row["fetched_at"]
    return d


def list_cached(
    conn: sqlite3.Connection,
    section_id: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    exclude_entry_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """section 의 캐시된 entries — 옵션으로 entry_date 범위 + entry_id 제외.

    날짜 비교는 캐시 entry_date 의 앞 8자리 (YYYYMMDD) 와 동일 형식 가정.
    호출자가 이미 화면에 보유한 entry_id 를 `exclude_entry_ids` 로 넘기면
    중복 제거 (현재 윈도우 + 캐시 보충 시나리오).
    """
    conn.row_factory = sqlite3.Row
    where = ["section_id = ?"]
    args: list[Any] = [section_id]
    if start_date:
        where.append("substr(entry_date, 1, 8) >= ?")
        args.append(start_date)
    if end_date:
        where.append("substr(entry_date, 1, 8) <= ?")
        args.append(end_date)
    sql = (
        "SELECT * FROM entries_cache "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY entry_date DESC, entry_id DESC"
    )
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    excl = exclude_entry_ids or set()
    for r in rows:
        if r["entry_id"] in excl:
            continue
        out.append(_row_to_entry(r))
    return out


def cached_oldest_date(
    conn: sqlite3.Connection, section_id: str,
) -> str | None:
    """section 의 캐시 중 가장 오래된 entry_date 8자리. 캐시 비었으면 None.

    호출자가 "캐시 범위 = oldest~today" 로 판단하고, 그 이전을 fetch 한다.
    """
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT substr(MIN(entry_date), 1, 8) AS d "
        "FROM entries_cache WHERE section_id = ?",
        (section_id,),
    ).fetchone()
    if row is None or row["d"] is None:
        return None
    return str(row["d"])


def cached_count(conn: sqlite3.Connection, section_id: str) -> int:
    """section 의 캐시된 entries 총 개수 — 디버깅 / 상태 표시."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM entries_cache WHERE section_id = ?",
        (section_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def purge_section(conn: sqlite3.Connection, section_id: str) -> int:
    """section 의 캐시 전체 삭제 — 명시적 invalidate. 반환: 삭제 row 수."""
    cur = conn.execute(
        "DELETE FROM entries_cache WHERE section_id = ?", (section_id,),
    )
    return cur.rowcount or 0


def remove_entry(
    conn: sqlite3.Connection, section_id: str, entry_id: str,
) -> bool:
    """단일 entry 캐시 삭제 — 후잉에서 delete 된 경우 호출자가 사용."""
    cur = conn.execute(
        "DELETE FROM entries_cache WHERE section_id = ? AND entry_id = ?",
        (section_id, entry_id),
    )
    return (cur.rowcount or 0) > 0
