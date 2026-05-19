"""DupeScanRepository — 중복 스캔 결과의 sqlite 영구화 (CL #52989+).

사용자 요청 (2026-05-19):
> 중복 거래 내역을 스캔한 다음 확정 전에 데이터베이스에 별도 테이블로
> 저장해주세요. 이후 중복 거래를 스캔할 때 데이터베이스를 확인해 정리되지
> 않은 항목은 후잉에 직접 요청하지 않고 일단 데이터베이스로부터 확보한
> 중복 항목에 출력해 정리하게 해주세요. 정리되지 않은 중복은 한번 가져오면
> 정리하기 전까지는 같은 날짜범위를 재요청하지 않도록 해주세요.

목적:
- `dupe_scan_clusters` 테이블의 CRUD 캡슐화.
- `EntriesScreen` worker + `DupeScanOverviewScreen` / `DuplicateScanScreen`
  이 같은 인스턴스를 공유.
- pure data layer — Textual / 후잉 의존 없음 (테스트 격리).

상태 모델:
- `pending`  — fetch 직후, 아직 정리 안 됨.
- `resolved` — 사용자가 cluster 안 일부/전체 삭제 + 확정 (Enter).
- `skipped`  — 사용자가 skip (n) 으로 넘김. 다음 스캔에서도 노출 (재검토).

policy:
- 동일 (section_id, scan_range_start, scan_range_end) 의 *pending* row 가
  하나라도 있으면 cache hit — 후잉 API 호출 X.
- skipped 만 남으면 사용자가 명시적으로 skip 한 것이므로 같은 의미로 cache
  hit (재검토하려면 refresh 버튼).
- 한 cluster 의 모든 member 가 후잉 server 에서 삭제됐어도 row 는 남음
  (resolved 마킹). 다음 스캔에서 새로 등장하지 않도록 보호.
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
class StoredCluster:
    """sqlite 에서 읽어온 cluster 1건.

    UI / worker 가 `whooing_core.dupes.DupeCluster` 와 동등하게 다룰 수
    있도록 같은 필드 + DB 식별자 (`id`, `status`).
    """

    id: int
    verdict: str
    reasons: tuple[str, ...]
    keep_suggestion: str | None
    entries: tuple[dict[str, Any], ...]
    status: str  # 'pending' | 'resolved' | 'skipped'


class DupeScanRepository:
    """중복 스캔 결과 sqlite 저장소 어댑터.

    호출자가 한 번 만들어 들고 다니며 같은 인스턴스 공유. 상태 없음 —
    매 호출이 connection 새로 열고 닫음 (TUI 의 다른 repo 와 같은 정책).
    """

    # ---- write ---------------------------------------------------------

    def save_scan(
        self,
        *,
        section_id: str,
        range_start: str,
        range_end: str,
        clusters: list,
    ) -> list[StoredCluster]:
        """스캔 결과를 sqlite 에 저장 후 DB id 가 채워진 StoredCluster 들 반환.

        같은 (section_id, range_start, range_end) 의 기존 pending/skipped
        row 는 *지우지 않는다* — 사용자가 refresh 명시 안 했으면 그대로 둠.
        호출자가 cache miss 후 처음 save 한다고 가정.

        만약 호출자가 refresh 의도라면 먼저 `clear_scan(...)` 호출.
        """
        if not clusters:
            return []
        now_iso = datetime.now(KST).isoformat()
        stored: list[StoredCluster] = []
        with tui_data.open_rw() as conn:
            for c in clusters:
                reasons = list(getattr(c, "reasons", ()) or ())
                entries = list(getattr(c, "entries", ()) or ())
                cur = conn.execute(
                    """INSERT INTO dupe_scan_clusters (
                        section_id, scan_range_start, scan_range_end,
                        scanned_at, verdict, reasons_json,
                        keep_suggestion, entries_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        section_id, range_start, range_end, now_iso,
                        c.verdict, json.dumps(reasons, ensure_ascii=False),
                        c.keep_suggestion,
                        json.dumps(entries, ensure_ascii=False),
                    ),
                )
                cluster_id = int(cur.lastrowid or 0)
                stored.append(StoredCluster(
                    id=cluster_id,
                    verdict=c.verdict,
                    reasons=tuple(reasons),
                    keep_suggestion=c.keep_suggestion,
                    entries=tuple(entries),
                    status="pending",
                ))
            conn.commit()
        return stored

    def update_status(
        self, cluster_id: int, status: str,
    ) -> None:
        """cluster 1건의 상태 갱신 (resolved / skipped).

        한 cluster 안 일부만 삭제하고 나머지를 keep 한 경우 — '정리 끝'
        의미라 resolved. 진짜 잘못된 cluster (false positive) 를 사용자가
        n 으로 넘긴 경우 — skipped.

        resolved/skipped 둘 다 다음 스캔에서 *재등장* 안 함 (skipped 도
        사용자 의도 = "이건 중복 아님" 으로 해석).
        """
        if status not in ("pending", "resolved", "skipped"):
            raise ValueError(f"invalid dupe scan cluster status: {status!r}")
        ts = datetime.now(KST).isoformat() if status != "pending" else None
        with tui_data.open_rw() as conn:
            conn.execute(
                "UPDATE dupe_scan_clusters SET status = ?, resolved_at = ? "
                "WHERE id = ?",
                (status, ts, cluster_id),
            )
            conn.commit()

    def clear_scan(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> int:
        """동일 (section_id, range) 의 모든 row 삭제 — refresh 진입점.

        반환: 삭제된 row 수. (사용자 확인 UI 가 N 건 잃는다고 안내할 때.)
        """
        with tui_data.open_rw() as conn:
            cur = conn.execute(
                "DELETE FROM dupe_scan_clusters "
                "WHERE section_id = ? AND scan_range_start = ? "
                "AND scan_range_end = ?",
                (section_id, range_start, range_end),
            )
            conn.commit()
            return int(cur.rowcount or 0)

    # ---- read ----------------------------------------------------------

    def load_open_clusters(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> list[StoredCluster]:
        """동일 (section_id, range) 의 status='pending' cluster 만 반환.

        worker 가 cache 조회 시 사용 — pending 이 하나라도 있으면 후잉
        API 호출 skip. 정렬: verdict 강한 순 → cluster 크기 큰 순 → 가장
        오래된 entry_date 순 (DupeScanOverview 도 같은 정렬 기대).
        """
        return self._load_filtered(
            section_id, range_start, range_end, statuses=("pending",),
        )

    def load_all_clusters(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> list[StoredCluster]:
        """동일 (section_id, range) 의 모든 status 반환 — overview 가
        resolved/skipped 도 표시할 때."""
        return self._load_filtered(
            section_id, range_start, range_end,
            statuses=("pending", "resolved", "skipped"),
        )

    def _load_filtered(
        self,
        section_id: str, range_start: str, range_end: str,
        *, statuses: tuple[str, ...],
    ) -> list[StoredCluster]:
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        sql = (
            "SELECT id, verdict, reasons_json, keep_suggestion, "
            "entries_json, status FROM dupe_scan_clusters "
            "WHERE section_id = ? AND scan_range_start = ? "
            "AND scan_range_end = ? "
            f"AND status IN ({placeholders})"
        )
        params: tuple = (section_id, range_start, range_end, *statuses)
        try:
            with tui_data.open_ro() as conn:
                rows = list(conn.execute(sql, params))
        except sqlite3.OperationalError:  # pragma: no cover — schema not yet init
            log.exception("load_filtered failed (schema not migrated?)")
            return []

        out: list[StoredCluster] = []
        for r in rows:
            try:
                reasons = tuple(json.loads(r["reasons_json"] or "[]"))
                entries = tuple(json.loads(r["entries_json"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                log.exception("dupe scan cluster row %s json malformed", r["id"])
                continue
            out.append(StoredCluster(
                id=int(r["id"]),
                verdict=str(r["verdict"]),
                reasons=tuple(str(x) for x in reasons),
                keep_suggestion=(
                    str(r["keep_suggestion"]) if r["keep_suggestion"] else None
                ),
                entries=entries,
                status=str(r["status"]),
            ))
        # 정렬 — verdict 강한 순 → cluster 크기 큰 순 → 첫 entry_date 오래된 순.
        verdict_order = {"identical": 3, "very_likely": 2, "possible": 1,
                         "different": 0}
        out.sort(key=lambda c: (
            -verdict_order.get(c.verdict, 0),
            -len(c.entries),
            _first_entry_date(c.entries),
        ))
        return out

    def has_open_scan(
        self, *, section_id: str, range_start: str, range_end: str,
    ) -> bool:
        """worker 의 cache 결정용 — 빠르게 boolean."""
        try:
            with tui_data.open_ro() as conn:
                row = conn.execute(
                    "SELECT 1 FROM dupe_scan_clusters "
                    "WHERE section_id = ? AND scan_range_start = ? "
                    "AND scan_range_end = ? AND status = 'pending' LIMIT 1",
                    (section_id, range_start, range_end),
                ).fetchone()
        except sqlite3.OperationalError:  # pragma: no cover
            return False
        return row is not None


def _first_entry_date(entries: tuple) -> str:
    if not entries:
        return ""
    first = entries[0]
    if not isinstance(first, dict):
        return ""
    return str(first.get("entry_date") or "")
