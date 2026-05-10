"""AttachmentBrowserScreen — entry 별 첨부파일 list / 추가 / 삭제.

- 'a': 파일 add (현재 모달은 path string input — 향후 file picker 로 교체).
- 'd': 선택 행 삭제 (delete_file=True 기본 — 휴지통 미구현, 영구 삭제).
- 'o': macOS `open` / Linux `xdg-open` 으로 외부 viewer 호출.
- 'r': 새로고침.

CL #51136+ (A11): 파일 크기 cap — `WHOOING_MAX_ATTACHMENT_BYTES` env (기본
100MB). 초과 시 `add_attachment` 가 ValueError 로 raise.

CL #51136+ (A4): P4 자동 submit 의 결과를 status 에 1줄 안내. 종전엔 silent
fire-and-forget 만 — 사용자가 첨부가 P4 에 안 올라간 줄 모를 수 있었음.
이제 background worker 가 끝나는 즉시 callback 으로 화면에 결과 표시.

저장 위치 (CL #51123+): `<project_root>/attachment/YYYY/YYYY-MM-DD/`.
sha256 dedup — 같은 파일 내용이 다른 entry 에 이미 첨부됐으면 디스크 추가
X (db row 만). 정확한 우선순위는 `whooing_tui.data.attachments_root()` 참고.

P4 자동 submit (CL #51124+):
- add 성공 시 → `[db, copied_file]` 한 CL 로 submit. description 은
  `p4_sync.describe_attachment_add(entry_id, filename, size, sha256)` 의
  기계적 한 줄 (LLM 미관여).
- remove 성공 + 파일까지 unlink 됐으면 → `[db, unlinked_path]` 로 submit
  (P4 가 디스크에서 사라진 파일을 `reconcile -d` 로 delete 처리).
- remove 가 dedup 보존 (다른 entry 가 같은 sha256 참조) 케이스면 → `[db]`
  만 submit, description 에 "db only, file kept" 명시.
- P4 환경 부재 시 모두 silent (`p4_sync` 내부 정책).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TextArea,
)

from whooing_core import attachments as core_attach
from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


def open_externally(path: str | Path) -> bool:
    """OS default app 으로 열기. 실패 시 False.

    CL #51140+ (A10): Windows 도 지원 — `os.startfile(path)` (PEP 277).
      - darwin → `open <path>`
      - linux  → `xdg-open <path>`
      - win32  → `os.startfile` (subprocess 가 아님 — Windows 표준).
    """
    p = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", p], check=True, timeout=5)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", p], check=True, timeout=5)
        elif sys.platform.startswith("win"):
            import os
            os.startfile(p)  # type: ignore[attr-defined]  # win32 only.
        else:
            return False
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    except OSError:  # pragma: no cover — Windows os.startfile 실패.
        return False


def list_for(
    entry_id: str, *, section_id: str | None = None,
) -> list[dict[str, Any]]:
    """db SELECT — 화면 외부에서도 호출 가능 (테스트 / preview 등).

    CL #51144+ (A5): `section_id` 명시 시 그 섹션의 첨부만. 같은 entry_id
    가 여러 섹션에서 보이는 cross-section 사고 방지. None 이면 종전 동작.
    """
    try:
        with tui_data.open_ro() as conn:
            m = core_attach.list_attachments_for(
                conn, [entry_id], section_id=section_id,
            )
    except FileNotFoundError:
        return []
    return m.get(entry_id, [])


# CL #51136+ (A11): 첨부 파일 크기 hard cap. 0 또는 음수면 cap 비활성.
# default = 100 MiB — P4 submit 의 30s timeout 안에서 충분히 처리되는 수준.
_DEFAULT_MAX_BYTES: int = 100 * 1024 * 1024


def _max_attachment_bytes() -> int:
    """`$WHOOING_MAX_ATTACHMENT_BYTES` 환경 변수로 override. <=0 면 비활성."""
    import os
    raw = os.getenv("WHOOING_MAX_ATTACHMENT_BYTES")
    if raw is None:
        return _DEFAULT_MAX_BYTES
    try:
        return int(raw)
    except ValueError:
        log.warning("WHOOING_MAX_ATTACHMENT_BYTES 가 정수 아님 — default 사용")
        return _DEFAULT_MAX_BYTES


def add_attachment(
    entry_id: str,
    src_path: str,
    note: str | None = None,
    section_id: str | None = None,
    *,
    on_p4_complete: Any = None,
) -> dict[str, Any]:
    """저장소에 복사 + db row insert + (CL #51124+) P4 submit.

    P4 환경 부재면 submit 은 silent skip — 본 함수의 return 값엔 영향 없음.
    fire-and-forget (background thread) 이라 함수 반환 직후 worker 가 reconcile
    + submit 진행. App 종료 직전 `p4_sync.flush_on_exit` 가 join.

    CL #51136+ (A11): 파일 크기가 `WHOOING_MAX_ATTACHMENT_BYTES` (기본 100MB)
    초과면 `ValueError` raise — caller (TUI) 가 사용자에게 안내.
    """
    src = Path(src_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    if not src.is_file():
        raise ValueError(f"not a file: {src}")
    # size cap.
    cap = _max_attachment_bytes()
    if cap > 0:
        size_now = src.stat().st_size
        if size_now > cap:
            raise ValueError(
                f"파일 크기 {size_now:,} bytes 가 cap {cap:,} bytes 초과 — "
                f"WHOOING_MAX_ATTACHMENT_BYTES 로 조정하거나 분할 후 재시도."
            )

    root = tui_data.attachments_root()
    copied, sha, size = core_attach.copy_to_attachments(
        src, attachments_root=root,
    )
    rel = str(copied.relative_to(root))
    from whooing_core import db as core_db
    with tui_data.open_rw() as conn:
        row = core_attach.upsert_attachment(
            conn,
            entry_id=entry_id,
            section_id=section_id,
            file_path=rel,
            original_path=str(src),
            original_filename=src.name,
            file_size_bytes=size,
            file_sha256=sha,
            mime_type=core_attach.detect_mime(src),
            note=note,
        )
        # CL #51147+ (A16): audit log.
        core_db.log_attachment_audit(
            conn,
            attachment_id=row.get("id"),
            entry_id=entry_id,
            action="add",
            details={
                "filename": src.name,
                "sha256": sha,
                "size_bytes": size,
                "section_id": section_id,
            },
        )
    # P4 자동 submit — db + 디스크 파일을 같은 CL 로 묶음. 환경 없으면 silent.
    from whooing_tui import p4_sync
    p4_sync.submit_files_to_p4(
        [tui_data.db_path(), copied],
        p4_sync.describe_attachment_add(
            entry_id=entry_id,
            filename=src.name,
            size_bytes=size,
            sha256=sha,
        ),
        on_complete=on_p4_complete,
    )
    return row


def _trash_enabled() -> bool:
    """`WHOOING_ATTACHMENT_TRASH=1` 면 휴지통 모드 — 영구 삭제 대신 mv.
    CL #51141+ (A13). default = False (종전 동작 — 영구 삭제)."""
    import os
    return os.getenv("WHOOING_ATTACHMENT_TRASH", "").lower() in ("1", "true", "yes")


def update_note(attachment_id: int, note: str | None) -> dict[str, Any] | None:
    """CL #51143+ (A9): 첨부의 note 만 갱신 + db P4 자동 submit.

    빈 문자열 / whitespace 는 NULL 로 정규화 (core helper 가 처리).
    매칭 row 없으면 None — caller 가 안내.

    CL #51147+ (A16): 변경 직후 audit log row 1건 (action="note_edit",
    details=before/after).
    """
    from whooing_core import db as core_db
    with tui_data.open_rw() as conn:
        # before 값 회수 (audit details 용).
        before_row = conn.execute(
            "SELECT note, original_filename, entry_id FROM entry_attachments "
            "WHERE id = ?", (attachment_id,),
        ).fetchone()
        before_note = (before_row["note"] if before_row else None)
        info = core_attach.update_attachment_note(conn, attachment_id, note)
        if info is not None and before_row is not None:
            core_db.log_attachment_audit(
                conn,
                attachment_id=attachment_id,
                entry_id=before_row["entry_id"],
                action="note_edit",
                details={
                    "filename": before_row["original_filename"],
                    "note_before": before_note,
                    "note_after": info.get("note"),
                },
            )
    if info is None:
        return None
    # P4 자동 submit (db 만 — 디스크 파일 변경 X).
    from whooing_tui import p4_sync
    eid = str(info.get("entry_id") or "")
    filename = (
        info.get("original_filename")
        or Path(info.get("file_path") or "").name
    )
    p4_sync.submit_db_to_p4(
        tui_data.db_path(),
        f"[whooing-tui] entry {eid} attachment {attachment_id} note edit: "
        f"{filename}",
    )
    return info


def remove(
    attachment_id: int, delete_file: bool = True, *, trash: bool | None = None,
) -> dict[str, Any] | None:
    """db row 삭제 + (delete_file=True 면) 디스크 파일 unlink/mv + P4 submit.

    CL #51124+: dedup 케이스 (같은 sha256 의 다른 row 가 남아있음) 면 디스크
    파일은 그대로 유지 + db 만 정리 — P4 description 도 그 사실을 명시.

    CL #51141+ (A13): `trash` 매개변수. None (default) 이면 env 변수 참고
    (`WHOOING_ATTACHMENT_TRASH`). True 면 영구 삭제 대신 `.trash/YYYYMMDD/`.
    """
    if trash is None:
        trash = _trash_enabled()
    root = tui_data.attachments_root()
    from whooing_core import db as core_db
    with tui_data.open_rw() as conn:
        info = core_attach.delete_attachment(
            conn, attachment_id,
            attachments_root=root,
            delete_file=delete_file,
            trash=trash,
        )
        if info is not None:
            core_db.log_attachment_audit(
                conn,
                attachment_id=attachment_id,
                entry_id=str(info.get("entry_id") or ""),
                action="delete",
                details={
                    "filename": info.get("original_filename"),
                    "sha256": info.get("file_sha256"),
                    "trashed": bool(info.get("file_trashed")),
                    "kept_other_refs": int(info.get("file_kept_other_refs") or 0),
                    "trash_path": info.get("trash_path"),
                },
            )
    if info is None:
        return None
    # P4 자동 submit — 케이스에 따라 paths / description 분기.
    from whooing_tui import p4_sync
    db = tui_data.db_path()
    filename = info.get("original_filename") or Path(info.get("file_path") or "").name
    file_rel = info.get("file_path") or ""
    full_path = root / file_rel if file_rel else None
    file_actually_deleted = bool(info.get("file_deleted"))
    file_trashed = bool(info.get("file_trashed"))   # CL #51141+ (A13).
    kept_other_refs = int(info.get("file_kept_other_refs") or 0)
    # P4 입장에선 unlink 와 mv (trash) 모두 원본 path 가 사라진 것 → reconcile -d.
    if (file_actually_deleted or file_trashed) and full_path is not None:
        paths = [db, full_path]
    else:
        paths = [db]
    p4_sync.submit_files_to_p4(
        paths,
        p4_sync.describe_attachment_delete(
            entry_id=str(info.get("entry_id") or ""),
            filename=str(filename),
            kept_other_refs=kept_other_refs,
        ),
    )
    return info


# ---- Modals --------------------------------------------------------


class _NoteEditModal(ModalScreen[str | None]):
    """CL #51143+ (A9): 첨부의 note 사후 편집.

    dismiss 값:
      - `None` — Esc 취소.
      - 그 외 string (`""` 포함) — note 갱신 ("" 은 caller 가 NULL 로 정규화).
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _NoteEditModal {
        align: center middle;
    }
    #notedit_box {
        background: $panel;
        border: thick $primary;
        padding: 1;
        width: 95%;
        max-width: 70;
        min-width: 30;
        height: auto;
    }
    #notedit_text {
        height: 6;
        margin: 1 0;
    }
    """

    def __init__(self, *, filename: str, initial: str = "") -> None:
        super().__init__()
        self._filename = filename
        self._initial = initial or ""

    def compose(self) -> ComposeResult:
        with Container(id="notedit_box"):
            yield Label(f"[bold]{self._filename}[/bold] — note 편집")
            yield Label("(자유 텍스트, 빈 값 저장 = 제거)")
            yield TextArea(self._initial, id="notedit_text")
            with Horizontal():
                yield Button("저장 (Ctrl+S)", id="notedit_ok", variant="primary")
                yield Button("Cancel (Esc)", id="notedit_cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#notedit_text", TextArea).focus()
        except Exception:  # pragma: no cover
            pass

    def action_save(self) -> None:
        try:
            value = self.query_one("#notedit_text", TextArea).text
        except Exception:  # pragma: no cover
            value = ""
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "notedit_ok":
            self.action_save()
        else:
            self.action_cancel()


class _AddPathModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "취소")]

    DEFAULT_CSS = """
    #addpath_box {
        /* CL #51120+: 좁은 터미널 대응. */
        background: $panel;
        border: thick $primary;
        padding: 1;
        width: 95%;
        max-width: 80;
        min-width: 30;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="addpath_box"):
            yield Label("첨부할 파일 경로 (절대):")
            yield Input(placeholder="/Users/me/Downloads/invoice.pdf", id="addpath_input")
            with Horizontal():
                yield Button("OK", id="addpath_ok", variant="primary")
                # CL #51139+ (A7): Browse 버튼 — FilePickerScreen.
                yield Button("Browse…", id="addpath_browse")
                yield Button("Cancel", id="addpath_cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "addpath_ok":
            self.dismiss(self.query_one("#addpath_input", Input).value or None)
        elif event.button.id == "addpath_browse":
            from whooing_tui.screens.file_picker import FilePickerScreen
            chosen = await self.app.push_screen_wait(FilePickerScreen(
                title="첨부할 파일 선택",
            ))
            if chosen:
                self.dismiss(chosen)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---- Main screen ----------------------------------------------------


class AttachmentBrowserScreen(Screen):
    """Entry 별 첨부파일 list / 추가 / 삭제.

    CL #51142+ (A12): 터미널 paste 로 들어온 텍스트가 절대 경로 + 존재하는
    파일이면 자동으로 첨부 후보로 인식 — 사용자가 `a` 누르고 path 입력하는
    step skip. terminal-emulator 의 file path drop / 사용자가 cmd+v 한 path
    모두 같은 Paste event.
    """

    BINDINGS = [
        Binding("escape", "back", "뒤로"),
        Binding("a", "add", "추가"),
        Binding("d", "delete", "삭제"),
        Binding("o", "open", "열기"),
        # CL #51143+ (A9): note 사후 편집 — 'e' (edit note).
        Binding("e", "edit_note", "Note 편집"),
        Binding("r", "refresh", "새로고침"),
    ]

    DEFAULT_CSS = """
    AttachmentBrowserScreen {
        layout: vertical;
    }
    #ab_status {
        height: auto;
        padding: 1;
        background: $boost;
    }
    """

    def __init__(self, entry_id: str, section_id: str | None = None) -> None:
        super().__init__()
        self.entry_id = entry_id
        self.section_id = section_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"Entry {self.entry_id} — 첨부", id="ab_status")
        yield DataTable(id="ab_table", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#ab_table", DataTable)
        table.cursor_type = "row"
        table.add_columns("id", "filename", "size", "mime", "attached_at", "note")
        self.action_refresh()

    def action_refresh(self) -> None:
        # CL #51144+ (A5): section 필터로 cross-section 누수 방지.
        rows = list_for(self.entry_id, section_id=self.section_id)
        table = self.query_one("#ab_table", DataTable)
        table.clear()
        for r in rows:
            table.add_row(
                str(r["id"]),
                r["original_filename"],
                _fmt_bytes(r.get("file_size_bytes")),
                r.get("mime_type") or "",
                r.get("attached_at", "")[:19],
                (r.get("note") or "")[:30],
                key=str(r["id"]),
            )
        self.query_one("#ab_status", Static).update(
            f"Entry {self.entry_id} — {len(rows)} 첨부"
        )

    async def action_add(self) -> None:
        path = await self.app.push_screen_wait(_AddPathModal())
        if not path:
            return
        # CL #51136+ (A4): P4 결과 callback. background worker 가 끝나면 호출 —
        # call_from_thread 로 main thread 에서 안전하게 status update.
        def _on_p4(status: str) -> None:
            label = {
                "ok": "P4 submit 완료",
                "no-changes": "P4: 변경 없음 (이미 동일)",
                "no-p4": "P4 환경 없음 — 로컬 only",
                "unmapped": "P4 workspace 매핑 외 — 로컬 only",
                "noop": "P4: 보낸 파일 없음",
                "error": "P4 submit 실패 (로그 참고)",
            }.get(status, f"P4: {status}")
            severity = "error" if status == "error" else "information"
            try:
                self.app.call_from_thread(
                    self.notify, label, severity=severity,
                )
            except Exception:  # pragma: no cover
                pass

        try:
            row = add_attachment(
                self.entry_id, path, section_id=self.section_id,
                on_p4_complete=_on_p4,
            )
        except (FileNotFoundError, ValueError) as ex:
            self.notify(f"추가 실패: {ex}", severity="error")
            return
        # CL #51140+ (A15): dedup 으로 기존 row 가 그대로 반환된 케이스 명시.
        # upsert_attachment 가 같은 (entry_id, sha256) 매칭 시 기존 row 반환 —
        # attached_at 이 과거면 그게 dedup 신호.
        from whooing_core.dates import KST
        from datetime import datetime
        att_at = str(row.get("attached_at") or "")
        is_dedup = False
        try:
            dt = datetime.fromisoformat(att_at)
            now = datetime.now(KST)
            # attached_at 이 5초 이상 과거 → dedup 으로 받은 기존 row.
            if (now - dt).total_seconds() > 5:
                is_dedup = True
        except (ValueError, TypeError):
            pass
        if is_dedup:
            self.notify(
                f"이미 첨부됨 (id={row.get('id')}, {att_at[:19]}) — "
                f"같은 파일이 이 거래에 이미 첨부돼 있습니다.",
                severity="warning",
            )
        else:
            self.notify(f"추가됨: id={row.get('id')}")
        self.action_refresh()

    def action_delete(self) -> None:
        table = self.query_one("#ab_table", DataTable)
        if not table.row_count:
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            aid = int(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        deleted = remove(aid, delete_file=True)
        if deleted:
            self.notify(f"삭제됨: id={aid}")
            self.action_refresh()
        else:
            self.notify(f"id={aid} 찾을 수 없음", severity="warning")

    def action_open(self) -> None:
        table = self.query_one("#ab_table", DataTable)
        if not table.row_count:
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            aid = int(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        # find the row in db (CL #51144+ section 필터).
        rows = list_for(self.entry_id, section_id=self.section_id)
        match = next((r for r in rows if r["id"] == aid), None)
        if not match:
            return
        full_path = tui_data.attachments_root() / match["file_path"]
        if open_externally(full_path):
            self.notify(f"열림: {full_path.name}")
        else:
            self.notify(f"열기 실패: {full_path}", severity="warning")

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_edit_note(self) -> None:
        """CL #51143+ (A9): 선택된 첨부의 note 사후 편집 — sync wrapper."""
        table = self.query_one("#ab_table", DataTable)
        if not table.row_count:
            self.notify("선택할 첨부가 없습니다.", severity="warning")
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            aid = int(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        rows = list_for(self.entry_id, section_id=self.section_id)
        match = next((r for r in rows if r["id"] == aid), None)
        if not match:
            self.notify(f"id={aid} 찾을 수 없음.", severity="warning")
            return
        self._edit_note_worker(
            aid,
            filename=str(match.get("original_filename") or ""),
            initial=str(match.get("note") or ""),
        )

    @work(exclusive=True, group="attach", name="edit_note")
    async def _edit_note_worker(
        self, aid: int, *, filename: str, initial: str,
    ) -> None:
        """`_NoteEditModal` push → save → db UPDATE + P4 submit."""
        result = await self.app.push_screen_wait(_NoteEditModal(
            filename=filename, initial=initial,
        ))
        if result is None:
            return  # Esc 취소.
        info = update_note(aid, result)
        if info is None:
            self.notify(f"id={aid} 갱신 실패.", severity="error")
            return
        new_note = info.get("note") or ""
        if new_note:
            self.notify(f"note 갱신: id={aid}")
        else:
            self.notify(f"note 제거: id={aid}")
        self.action_refresh()

    # ---- CL #51142+ (A12) paste / drop 흡수 -----------------------------

    def on_paste(self, event: Any) -> None:
        """터미널 paste 의 텍스트가 단일 절대 경로 + 존재하는 파일이면 자동 첨부.

        텍스트 형태:
          - 단일 path: `/Users/me/x.pdf`
          - file:// URL: `file:///Users/me/x.pdf`
          - 따옴표 둘러싼: `"/Users/me/x.pdf"`
          - 여러 줄 — 첫 줄만.

        매칭 안 되면 silent (paste 가 다른 위젯에 도달하도록).
        """
        text = getattr(event, "text", "") or ""
        first = text.splitlines()[0].strip() if text else ""
        # 따옴표 strip.
        if first.startswith('"') and first.endswith('"') and len(first) >= 2:
            first = first[1:-1]
        elif first.startswith("'") and first.endswith("'") and len(first) >= 2:
            first = first[1:-1]
        # file:// URL 처리.
        if first.startswith("file://"):
            from urllib.parse import unquote, urlparse
            parsed = urlparse(first)
            first = unquote(parsed.path)
        if not first:
            return
        from pathlib import Path
        p = Path(first).expanduser()
        if not (p.is_absolute() and p.is_file()):
            return
        # 자동 첨부.
        try:
            row = add_attachment(
                self.entry_id, str(p), section_id=self.section_id,
            )
        except (FileNotFoundError, ValueError) as ex:
            self.notify(f"paste 첨부 실패: {ex}", severity="error")
            return
        self.notify(f"📋 paste 첨부: {p.name} (id={row.get('id')})")
        self.action_refresh()


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
