"""AttachmentBrowserScreen — entry 별 첨부파일 list / 추가 / 삭제.

- 'a': 파일 add (현재 모달은 path string input — 향후 file picker 로 교체).
- 'd': 선택 행 삭제 (delete_file=True 기본).
- 'o': macOS `open` / Linux `xdg-open` 으로 외부 viewer 호출.
- 'r': 새로고침.

저장 위치: $WHOOING_DATA_DIR/attachments/YYYY/YYYY-MM-DD/. sha256 dedup —
같은 파일 내용이 다른 entry 에 이미 첨부됐으면 디스크 추가 X (db row 만).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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
)

from whooing_core import attachments as core_attach
from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


def open_externally(path: str | Path) -> bool:
    """OS default app 으로 열기 (macOS / Linux). 실패 시 False."""
    p = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", p], check=True, timeout=5)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", p], check=True, timeout=5)
        else:
            return False  # Windows etc. — TUI 가 띄우는 환경에서 우선순위 X
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def list_for(entry_id: str) -> list[dict[str, Any]]:
    """db SELECT — 화면 외부에서도 호출 가능 (테스트 / preview 등)."""
    try:
        with tui_data.open_ro() as conn:
            m = core_attach.list_attachments_for(conn, [entry_id])
    except FileNotFoundError:
        return []
    return m.get(entry_id, [])


def add_attachment(
    entry_id: str,
    src_path: str,
    note: str | None = None,
    section_id: str | None = None,
) -> dict[str, Any]:
    """저장소에 복사 + db row insert. 같은 sha 면 dedup."""
    src = Path(src_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)
    if not src.is_file():
        raise ValueError(f"not a file: {src}")

    root = tui_data.attachments_root()
    copied, sha, size = core_attach.copy_to_attachments(
        src, attachments_root=root,
    )
    rel = str(copied.relative_to(root))
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
    return row


def remove(attachment_id: int, delete_file: bool = True) -> dict[str, Any] | None:
    with tui_data.open_rw() as conn:
        return core_attach.delete_attachment(
            conn, attachment_id,
            attachments_root=tui_data.attachments_root(),
            delete_file=delete_file,
        )


# ---- Modals --------------------------------------------------------


class _AddPathModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "취소")]

    DEFAULT_CSS = """
    #addpath_box {
        background: $panel;
        border: thick $primary;
        padding: 1;
        width: 80;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="addpath_box"):
            yield Label("첨부할 파일 경로 (절대):")
            yield Input(placeholder="/Users/me/Downloads/invoice.pdf", id="addpath_input")
            with Horizontal():
                yield Button("OK", id="addpath_ok", variant="primary")
                yield Button("Cancel", id="addpath_cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "addpath_ok":
            self.dismiss(self.query_one("#addpath_input", Input).value or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---- Main screen ----------------------------------------------------


class AttachmentBrowserScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "뒤로"),
        Binding("a", "add", "추가"),
        Binding("d", "delete", "삭제"),
        Binding("o", "open", "열기"),
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
        rows = list_for(self.entry_id)
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
        try:
            row = add_attachment(self.entry_id, path, section_id=self.section_id)
        except (FileNotFoundError, ValueError) as ex:
            self.notify(f"추가 실패: {ex}", severity="error")
            return
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
        # find the row in db
        rows = list_for(self.entry_id)
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


def _fmt_bytes(n: int | None) -> str:
    if not n:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
