"""EntryRepository — 로컬 sqlite 의 entry annotation / 해시태그 / 첨부 카운트
조회·갱신을 한 클래스에 모은다 (CL #52834+).

종전엔 `screens/entries.py` (~3000 줄 god-object) 가 모든 sqlite 접근을
직접 — `tui_data.open_rw()` / `core_db.X` / `core_attach.X` 가 화면 안에
산재. 이 모듈은 그 호출들을 캡슐화해 다음을 가능하게 한다:

- 다른 화면 (예: DupeEvalScreen, AttachmentBrowser) 이 *같은* repo 인스턴스
  를 받아 일관된 인터페이스로 사용.
- 단위 테스트가 fake repo 를 주입해 화면을 db 의존 없이 검증.
- 향후 sqlite 외 backend (예: SQLAlchemy) 로 갈 때 영향 범위 작게.

스코프 (CL #52834+ 1차):
- 읽기: `tags_for(entry_id)`, `tags_for_many(entry_ids)`, `all_tags()`,
  `attachment_counts(entry_ids, section_id)`, `tag_colors(section_id)`.
- 쓰기: `persist(entry_id, section_id, memo, tags)`,
  `purge(entry_id)`.

후잉 API 호출은 *본 repo 에 두지 않는다* — `WhooingClient` 가 별도. 본
repo 는 로컬 sqlite + 첨부 디스크 + P4 자동 submit 까지만 책임.

EntriesScreen 의 기존 private 메서드 (`_persist_local`, `_purge_local`,
`_fetch_local_tags` 등) 는 본 repo 의 thin wrapper 로 남겨 후방 호환.
"""

from __future__ import annotations

import logging
from typing import Any

from whooing_core import db as core_db

from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


class EntryRepository:
    """Entry annotation / 해시태그 / 첨부 카운트 의 로컬 저장소 어댑터.

    상태가 없다 (idempotent). 화면이 보통 `self._repo = EntryRepository()`
    를 한 번 만들어 들고 다닌다.
    """

    # ---- read --------------------------------------------------------

    def tags_for(self, entry_id: str) -> list[str]:
        """단일 entry 의 해시태그 list. 실패 / 미존재 시 빈 list."""
        if not entry_id:
            return []
        try:
            with tui_data.open_ro() as conn:
                rows = core_db.get_annotations_for(conn, [str(entry_id)])
        except Exception:  # pragma: no cover — db 미존재 등
            log.exception("EntryRepository.tags_for failed")
            return []
        info = rows.get(str(entry_id)) or {}
        return list(info.get("hashtags") or [])

    def tags_for_many(
        self, entry_ids: list[str],
    ) -> dict[str, list[str]]:
        """여러 entry 의 해시태그 batch. 결과는 *태그 있는 entry 만*.

        item 컬럼 인라인 표시 / 태그 필터에서 같은 source 사용.
        """
        if not entry_ids:
            return {}
        try:
            with tui_data.open_ro() as conn:
                rows = core_db.get_annotations_for(conn, list(entry_ids))
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.tags_for_many failed")
            return {}
        return {
            eid: list(info.get("hashtags") or [])
            for eid, info in rows.items()
            if info.get("hashtags")
        }

    def all_tags(self) -> dict[str, int]:
        """전체 해시태그 사전 `{tag: count}` — TagsPickerScreen 자주쓰는 태그
        등에서 사용. 실패는 빈 사전.
        """
        try:
            with tui_data.open_ro() as conn:
                return core_db.list_hashtags(conn)
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.all_tags failed")
            return {}

    def attachment_counts(
        self, entry_ids: list[str], *, section_id: str | None = None,
    ) -> dict[str, int]:
        """entry_id list → 첨부 개수 사전. 0개 entry 는 결과에서 빠짐.

        CL #51144+ (A5): section 격리 — 다른 섹션의 첨부가 카운트에 새지 않게.
        """
        if not entry_ids:
            return {}
        try:
            from whooing_core import attachments as core_attach
            with tui_data.open_ro() as conn:
                m = core_attach.list_attachments_for(
                    conn, list(entry_ids), section_id=section_id,
                )
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.attachment_counts failed")
            return {}
        return {eid: len(rows) for eid, rows in m.items() if rows}

    def tag_colors(self, *, section_id: str | None = None) -> dict[str, str]:
        """CL #51151+ (H11): {tag: color}. 실패는 빈 사전."""
        try:
            with tui_data.open_ro() as conn:
                return core_db.get_tag_colors(conn, section_id=section_id)
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.tag_colors failed")
            return {}

    # ---- write -------------------------------------------------------

    def persist(
        self,
        *,
        entry_id: str,
        section_id: str | None,
        memo: str,
        tags: list[str],
    ) -> None:
        """후잉 mutation 성공 후 로컬 sqlite 에 memo + 해시태그 동기화.

        memo 는 후잉과 동일값 mirror (검색·통계용 로컬 인덱스). tags 는 로컬
        전용. 둘 다 비었을 때도 annotation row 는 만들어 둠 (set_hashtags
        가 FK 위해 강제 생성).

        write 직후 P4 자동 submit (P4 환경 없으면 silent).
        """
        if not entry_id:
            return
        previous_tags: list[str] = []  # CL #51141+ (H10) — P4 desc diff.
        try:
            with tui_data.open_rw() as conn:
                prev = core_db.get_annotations_for(conn, [str(entry_id)])
                previous_tags = list(
                    prev.get(str(entry_id), {}).get("hashtags", []) or []
                )
                core_db.upsert_annotation(
                    conn,
                    entry_id=str(entry_id),
                    section_id=section_id or None,
                    note=memo or None,
                )
                core_db.set_hashtags(
                    conn, str(entry_id), list(tags or []),
                    section_id=section_id or None,
                )
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.persist failed")
            return
        from whooing_tui import sync
        sync.submit_db(
            tui_data.db_path(),
            sync.describe_annotation(
                entry_id=str(entry_id),
                memo_changed=bool(memo),
                tags=list(tags or []),
                previous_tags=previous_tags,
            ),
        )

    def migrate_local(
        self, *, old_entry_id: str, new_entry_id: str,
        section_id: str | None = None,
    ) -> None:
        """복원(시나리오 11 안 B)으로 후잉 entry_id 가 바뀔 때, 로컬 메모/
        해시태그/첨부를 old → new entry_id 로 재키잉. 첨부 디스크 파일은 그대로
        (db 의 entry_id 만 갱신). write 후 P4 자동 submit.
        """
        if not old_entry_id or not new_entry_id or old_entry_id == new_entry_id:
            return
        try:
            with tui_data.open_rw() as conn:
                conn.execute(
                    "UPDATE OR IGNORE entry_annotations SET entry_id = ? "
                    "WHERE entry_id = ?", (new_entry_id, old_entry_id),
                )
                conn.execute(
                    "UPDATE OR IGNORE entry_hashtags SET entry_id = ? "
                    "WHERE entry_id = ?", (new_entry_id, old_entry_id),
                )
                conn.execute(
                    "UPDATE entry_attachments SET entry_id = ? "
                    "WHERE entry_id = ?", (new_entry_id, old_entry_id),
                )
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.migrate_local failed")
            return
        from whooing_tui import sync
        sync.submit_db(
            tui_data.db_path(),
            f"[whooing-tui] entry {old_entry_id}→{new_entry_id} 로컬 메타 "
            f"재키잉 (복원)",
        )

    def purge(self, entry_id: str) -> None:
        """삭제된 거래의 annotation / 해시태그 / 첨부 모두 정리.

        CL #51132+ (A1): 첨부 row 외 디스크 파일까지 함께. P4 submit
        에 사라진 파일 path 도 포함시켜 reconcile -d 가 작동.
        """
        if not entry_id:
            return
        deleted_files: list = []
        try:
            from whooing_core import attachments as core_attach
            root = tui_data.attachments_root()
            with tui_data.open_rw() as conn:
                core_db.remove_annotation(conn, str(entry_id))
                purged = core_attach.purge_attachments_for_entry(
                    conn, str(entry_id), attachments_root=root,
                )
                for p in purged:
                    if p.get("file_deleted"):
                        deleted_files.append(root / p["file_path"])
        except Exception:  # pragma: no cover
            log.exception("EntryRepository.purge failed")
            return
        from whooing_tui import sync
        paths = [tui_data.db_path(), *deleted_files]
        sync.submit_files(
            paths,
            sync.describe_annotation(
                entry_id=str(entry_id),
                memo_changed=False, tags=None, deleted=True,
            ),
        )


__all__ = ["EntryRepository"]
