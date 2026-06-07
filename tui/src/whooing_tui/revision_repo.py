"""EntryRevisionRepository — 거래 수정 이력 + 소프트삭제(안 B) 의 TUI 어댑터.

`core.revisions` (순수/sqlite 함수) 를 `tui_data.open_rw/open_ro` + P4 자동
submit 으로 감싼다. `EntryRepository` (annotation/태그/첨부) 와 형제 — 후잉
REST 호출은 *하지 않는다* (화면 워커가 후잉 mutation 과 본 기록을 묶는다).

설계: docs/scenarios/11-edit-history-and-soft-delete.md
"""

from __future__ import annotations

import logging
from typing import Any

from whooing_core import entries_cache as core_cache
from whooing_core import revisions as core_rev

from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


class EntryRevisionRepository:
    """수정 이력 로컬 저장소 어댑터. 상태 없음 (idempotent)."""

    # ---- write (후잉 mutation 성공 *후* 호출) -------------------------

    def record_create(
        self, *, entry: dict[str, Any], section_id: str | None,
    ) -> str | None:
        """새 거래 생성 — op=create baseline 1버전. 반환 = logical_id(=새 entry_id)."""
        eid = str(entry.get("entry_id") or "")
        if not eid:
            return None
        try:
            with tui_data.open_rw() as conn:
                lid = core_rev.logical_id_for_entry(conn, eid)
                if lid:
                    return lid  # 이미 추적 중 (재진입 안전).
                rev_no = core_rev.record_revision(
                    conn, logical_id=eid, whooing_entry_id=eid,
                    section_id=section_id, op=core_rev.OP_CREATE,
                    snapshot=core_rev.snapshot_fields(entry),
                )
        except Exception:  # pragma: no cover
            log.exception("record_create failed")
            return None
        self._submit("create", eid, eid, rev_no, None)
        return eid

    def record_edit(
        self, *, prev: dict[str, Any], new: dict[str, Any],
        section_id: str | None,
    ) -> str | None:
        """수정 — 직전 상태 baseline 보장 후 op=edit 새 버전. 제자리 갱신이라
        whooing_entry_id 불변. 반환 = logical_id."""
        eid = str(new.get("entry_id") or prev.get("entry_id") or "")
        if not eid:
            return None
        summary = core_rev.summarize_diff(
            core_rev.diff(
                core_rev.snapshot_fields(prev), core_rev.snapshot_fields(new),
            )
        )
        try:
            with tui_data.open_rw() as conn:
                lid = core_rev.ensure_baseline(
                    conn, entry=prev, section_id=section_id,
                )
                rev_no = core_rev.record_revision(
                    conn, logical_id=lid, whooing_entry_id=eid,
                    section_id=section_id, op=core_rev.OP_EDIT,
                    snapshot=core_rev.snapshot_fields(new),
                    note=summary,
                )
                # entries_cache 도 새 값으로 (refresh 전 일관성).
                core_cache.upsert_entries(conn, section_id or "", [new])
        except Exception:  # pragma: no cover
            log.exception("record_edit failed")
            return None
        self._submit("edit", lid, eid, rev_no, summary)
        return lid

    def record_delete(
        self, *, entry: dict[str, Any], section_id: str | None,
    ) -> str | None:
        """삭제(안 B: 후잉 실삭제 *후* 호출) — op=delete, is_deleted, 후잉 id
        보존 안 함(None). entries_cache 에서도 제거. 반환 = logical_id."""
        eid = str(entry.get("entry_id") or "")
        if not eid:
            return None
        try:
            with tui_data.open_rw() as conn:
                lid = core_rev.ensure_baseline(
                    conn, entry=entry, section_id=section_id,
                )
                rev_no = core_rev.record_revision(
                    conn, logical_id=lid, whooing_entry_id=None,
                    section_id=section_id, op=core_rev.OP_DELETE,
                    snapshot=core_rev.snapshot_fields(entry),
                    is_deleted=True,
                )
                if section_id:
                    core_cache.remove_entry(conn, section_id, eid)
        except Exception:  # pragma: no cover
            log.exception("record_delete failed")
            return None
        self._submit("delete", lid, None, rev_no, None)
        return lid

    def record_restore(
        self, *, logical_id: str, new_entry: dict[str, Any],
        section_id: str | None, reverted_from: int | None = None,
    ) -> int | None:
        """복원(휴지통→후잉 재생성 *후* 호출) — op=restore, 새 whooing_entry_id.
        반환 = 새 revision_no."""
        eid = str(new_entry.get("entry_id") or "")
        if not logical_id or not eid:
            return None
        try:
            with tui_data.open_rw() as conn:
                rev_no = core_rev.record_revision(
                    conn, logical_id=logical_id, whooing_entry_id=eid,
                    section_id=section_id, op=core_rev.OP_RESTORE,
                    snapshot=core_rev.snapshot_fields(new_entry),
                    reverted_from=reverted_from,
                )
                core_cache.upsert_entries(conn, section_id or "", [new_entry])
        except Exception:  # pragma: no cover
            log.exception("record_restore failed")
            return None
        self._submit("restore", logical_id, eid, rev_no, None)
        return rev_no

    def record_revert(
        self, *, logical_id: str, new_entry: dict[str, Any],
        section_id: str | None, reverted_from: int | None,
    ) -> int | None:
        """되돌리기(살아있는 거래 제자리 갱신 *후* 호출) — op=revert. 반환=rev_no."""
        eid = str(new_entry.get("entry_id") or "")
        if not logical_id or not eid:
            return None
        try:
            with tui_data.open_rw() as conn:
                rev_no = core_rev.record_revision(
                    conn, logical_id=logical_id, whooing_entry_id=eid,
                    section_id=section_id, op=core_rev.OP_REVERT,
                    snapshot=core_rev.snapshot_fields(new_entry),
                    reverted_from=reverted_from,
                )
                core_cache.upsert_entries(conn, section_id or "", [new_entry])
        except Exception:  # pragma: no cover
            log.exception("record_revert failed")
            return None
        self._submit("revert", logical_id, eid, rev_no, None)
        return rev_no

    def reconcile_external(
        self, *, section_id: str | None, current_entries: list[dict[str, Any]],
    ) -> list[str]:
        """추적 중인 거래의 TUI 밖 수정을 op=external 로 흡수. 반환=영향 logical."""
        if not section_id or not current_entries:
            return []
        try:
            with tui_data.open_rw() as conn:
                affected = core_rev.reconcile_external(
                    conn, section_id=section_id, current_entries=current_entries,
                )
        except Exception:  # pragma: no cover
            log.exception("reconcile_external failed")
            return []
        if affected:
            self._submit(
                "external", affected[0],
                None, None, f"{len(affected)}건 외부 변경 흡수",
            )
        return affected

    def purge(self, logical_id: str) -> None:
        """영구삭제 — 한 logical 의 모든 버전 + head 제거 (파괴적)."""
        if not logical_id:
            return
        try:
            with tui_data.open_rw() as conn:
                core_rev.purge_logical(conn, logical_id)
        except Exception:  # pragma: no cover
            log.exception("purge failed")
            return
        self._submit("purge", logical_id, None, None, None)

    # ---- read --------------------------------------------------------

    def logical_for_entry(self, entry_id: str) -> str | None:
        if not entry_id:
            return None
        try:
            with tui_data.open_ro() as conn:
                return core_rev.logical_id_for_entry(conn, str(entry_id))
        except Exception:  # pragma: no cover
            log.exception("logical_for_entry failed")
            return None

    def revisions_for(self, logical_id: str) -> list[dict[str, Any]]:
        if not logical_id:
            return []
        try:
            with tui_data.open_ro() as conn:
                return core_rev.list_revisions(conn, logical_id)
        except Exception:  # pragma: no cover
            log.exception("revisions_for failed")
            return []

    def head(self, logical_id: str) -> dict[str, Any] | None:
        try:
            with tui_data.open_ro() as conn:
                return core_rev.head_for(conn, logical_id)
        except Exception:  # pragma: no cover
            log.exception("head failed")
            return None

    def list_deleted(self, section_id: str | None = None) -> list[dict[str, Any]]:
        try:
            with tui_data.open_ro() as conn:
                return core_rev.list_deleted(conn, section_id)
        except Exception:  # pragma: no cover
            log.exception("list_deleted failed")
            return []

    # ---- 내부: P4 자동 submit ----------------------------------------

    def _submit(
        self, op: str, logical_id: str, entry_id: str | None,
        revision_no: int | None, summary: str | None,
    ) -> None:
        from whooing_tui import p4_sync
        try:
            p4_sync.submit_db_to_p4(
                tui_data.db_path(),
                p4_sync.describe_revision(
                    op=op, logical_id=logical_id, entry_id=entry_id,
                    revision_no=revision_no, summary=summary,
                ),
            )
        except Exception:  # pragma: no cover
            log.debug("revision P4 submit enqueue failed (silent)", exc_info=True)


__all__ = ["EntryRevisionRepository"]
