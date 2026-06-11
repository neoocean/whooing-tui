"""RecurringScanRepository — 반복거래누락 스캔 결과의 sqlite 영구화.

`DupeScanRepository` 와 대칭. 반복 시리즈의 누락 회차를 `recurring_scan_series`
테이블에 저장하고, 같은 (section_id, range) 의 pending 이 있으면 후잉 재요청
없이 sqlite 에서 로드한다.

상태 모델:
- `pending`   — fetch 직후, 아직 검토 안 함.
- `handled`   — 사용자가 누락분을 입력하거나 직접 처리함.
- `dismissed` — 실제 반복이 아니거나 의도적으로 중단된 시리즈 (다시 보지 않음).

policy:
- 동일 (section_id, scan_range_start, scan_range_end) 의 *pending* row 가
  하나라도 있으면 cache hit — 후잉 API 호출 X.
- handled / dismissed 둘 다 다음 스캔에서 재등장 안 함.

pure data layer — Textual / 후잉 의존 없음 (테스트 격리).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from whooing_core.dates import KST

from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


@dataclass
class StoredSeries:
    """sqlite 에서 읽어온 반복 시리즈 1건.

    `whooing_core.recurring.RecurringSeries` 와 동등하게 다룰 수 있도록 같은
    표시 필드 + DB 식별자 (`id`, `status`). missing 은 dict list 로 — UI 가
    expected_date / kind 키로 접근.
    """

    id: int
    l_account_id: str
    r_account_id: str
    item: str
    cadence: str
    typical_money: int | None
    occurrences: int
    last_date: str
    missing: tuple[dict[str, Any], ...]
    sample: dict[str, Any]
    status: str  # 'pending' | 'handled' | 'dismissed'

    @property
    def has_overdue(self) -> bool:
        return any(m.get("kind") == "overdue" for m in self.missing)


# 심각도 정렬 — overdue 우선 → 누락 수 → 회차 수 → 금액 (detect 와 동일 정책).
def _sort_key(s: StoredSeries) -> tuple:
    return (
        not s.has_overdue,
        -len(s.missing),
        -s.occurrences,
        -(s.typical_money or 0),
    )


class RecurringScanRepository:
    """반복거래누락 스캔 결과 sqlite 저장소 어댑터.

    상태 없음 — 매 호출이 connection 새로 열고 닫음 (다른 repo 와 같은 정책).
    """

    # ---- write ---------------------------------------------------------

    def save_scan(
        self,
        *,
        section_id: str,
        range_start: str,
        range_end: str,
        series: list,
    ) -> list[StoredSeries]:
        """스캔 결과(RecurringSeries list)를 sqlite 에 저장 후 id 채워 반환.

        기존 row 는 지우지 않는다 — refresh 의도면 호출자가 먼저 clear_scan.
        """
        if not series:
            return []
        now_iso = datetime.now(KST).isoformat()
        stored: list[StoredSeries] = []
        with tui_data.open_rw() as conn:
            for s in series:
                missing = [
                    {"expected_date": m.expected_date, "kind": m.kind}
                    for m in getattr(s, "missing", ()) or ()
                ]
                snapshot = {
                    "l_account_id": s.l_account_id,
                    "r_account_id": s.r_account_id,
                    "item": s.item,
                    "item_norm": s.item_norm,
                    "cadence": s.cadence,
                    "period_days": s.period_days,
                    "occurrences": s.occurrences,
                    "first_date": s.first_date,
                    "last_date": s.last_date,
                    "typical_money": s.typical_money,
                    "sample": s.sample,
                    "entry_ids": list(s.entry_ids),
                    "regularity": s.regularity,
                    "discontinued": s.discontinued,
                    "missing": missing,
                }
                cur = conn.execute(
                    """INSERT INTO recurring_scan_series (
                        section_id, scan_range_start, scan_range_end,
                        scanned_at, l_account_id, r_account_id, item,
                        cadence, typical_money, occurrences, last_date,
                        missing_json, series_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        section_id, range_start, range_end, now_iso,
                        s.l_account_id, s.r_account_id, s.item,
                        s.cadence, s.typical_money, s.occurrences, s.last_date,
                        json.dumps(missing, ensure_ascii=False),
                        json.dumps(snapshot, ensure_ascii=False),
                    ),
                )
                stored.append(StoredSeries(
                    id=int(cur.lastrowid or 0),
                    l_account_id=s.l_account_id,
                    r_account_id=s.r_account_id,
                    item=s.item,
                    cadence=s.cadence,
                    typical_money=s.typical_money,
                    occurrences=s.occurrences,
                    last_date=s.last_date,
                    missing=tuple(missing),
                    sample=s.sample,
                    status="pending",
                ))
            conn.commit()
        stored.sort(key=_sort_key)
        return stored

    def update_status(self, series_id: int, status: str) -> None:
        """시리즈 1건의 상태 갱신 (handled / dismissed)."""
        if status not in ("pending", "handled", "dismissed"):
            raise ValueError(f"invalid recurring scan status: {status!r}")
        ts = datetime.now(KST).isoformat() if status != "pending" else None
        with tui_data.open_rw() as conn:
            conn.execute(
                "UPDATE recurring_scan_series SET status = ?, resolved_at = ? "
                "WHERE id = ?",
                (status, ts, series_id),
            )
            conn.commit()

    def clear_scan(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> int:
        """동일 (section_id, range) 의 모든 row 삭제 — refresh 진입점."""
        with tui_data.open_rw() as conn:
            cur = conn.execute(
                "DELETE FROM recurring_scan_series "
                "WHERE section_id = ? AND scan_range_start = ? "
                "AND scan_range_end = ?",
                (section_id, range_start, range_end),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    # ---- read ----------------------------------------------------------

    def load_open_series(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> list[StoredSeries]:
        """status='pending' 시리즈만 반환 (cache 조회용)."""
        return self._load_filtered(
            section_id, range_start, range_end, statuses=("pending",),
        )

    def load_all_series(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> list[StoredSeries]:
        """모든 status 반환 — overview 가 handled/dismissed 도 표시할 때."""
        return self._load_filtered(
            section_id, range_start, range_end,
            statuses=("pending", "handled", "dismissed"),
        )

    def has_open_scan(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> bool:
        """worker 의 cache 결정용 — 빠르게 boolean."""
        try:
            with tui_data.open_ro() as conn:
                row = conn.execute(
                    "SELECT 1 FROM recurring_scan_series "
                    "WHERE section_id = ? AND scan_range_start = ? "
                    "AND scan_range_end = ? AND status = 'pending' LIMIT 1",
                    (section_id, range_start, range_end),
                ).fetchone()
        except sqlite3.OperationalError:  # pragma: no cover
            return False
        return row is not None

    def _load_filtered(
        self,
        section_id: str, range_start: str, range_end: str,
        *, statuses: tuple[str, ...],
    ) -> list[StoredSeries]:
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        sql = (
            "SELECT id, l_account_id, r_account_id, item, cadence, "
            "typical_money, occurrences, last_date, missing_json, "
            "series_json, status FROM recurring_scan_series "
            "WHERE section_id = ? AND scan_range_start = ? "
            "AND scan_range_end = ? "
            f"AND status IN ({placeholders})"
        )
        params: tuple = (section_id, range_start, range_end, *statuses)
        try:
            with tui_data.open_ro() as conn:
                rows = list(conn.execute(sql, params))
        except sqlite3.OperationalError:  # pragma: no cover — schema not yet init
            log.exception("recurring load_filtered failed (schema not migrated?)")
            return []

        out: list[StoredSeries] = []
        for r in rows:
            try:
                missing = tuple(json.loads(r["missing_json"] or "[]"))
                snapshot = json.loads(r["series_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                log.exception("recurring series row %s json malformed", r["id"])
                continue
            sample = snapshot.get("sample") if isinstance(snapshot, dict) else {}
            out.append(StoredSeries(
                id=int(r["id"]),
                l_account_id=str(r["l_account_id"] or ""),
                r_account_id=str(r["r_account_id"] or ""),
                item=str(r["item"] or ""),
                cadence=str(r["cadence"] or ""),
                typical_money=(
                    int(r["typical_money"])
                    if r["typical_money"] is not None else None
                ),
                occurrences=int(r["occurrences"] or 0),
                last_date=str(r["last_date"] or ""),
                missing=missing,
                sample=sample if isinstance(sample, dict) else {},
                status=str(r["status"]),
            ))
        out.sort(key=_sort_key)
        return out
