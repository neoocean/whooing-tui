"""ReportCustomsScreen — 사용자 정의 보고서 행 관리 (생성/삭제) (0.84.0).

로드맵 P2-C. 재무상태표(report_bs)/손익계산서(report_pl) 하단의 사용자 정의
합산 행. 종전엔 `screens/reports.py` 에서 *읽기만* 가능했다 — 이제 직접
추가/삭제한다.

행 모델: `title` + `plus[]`/`minus[]` (항목 참조 "`<account>_<account_id|total>`",
예: "assets_x11", "liabilities_total") + `addminus` 공식 (x=plus−minus 합,
예: "x*0.1"). 모든 호출은 공식 후잉 MCP `report_customs-*` 위임.

키: ↑/↓ 선택 · t 보고서 전환(BS↔PL) · n 신규 · d 삭제 · r 재로드 · q/Esc 뒤로.
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static

from whooing_tui.client import WhooingClient
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.state import SessionState
from whooing_tui.widgets import (
    ConfirmModal as _ConfirmModal,
    MenuBar, MenuBarMixin, MenuItem, MenuSpec, menubar_bindings,
)

log = logging.getLogger(__name__)

_REPORTS = ("report_bs", "report_pl")
_REPORT_KR = {"report_bs": "재무상태표(BS)", "report_pl": "손익계산서(PL)"}


def _parse_refs(raw: str) -> list[str]:
    """콤마 구분 항목 참조 문자열 → list. 빈 원소 제거."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


class _ReportCustomEditModal(ModalScreen[dict | None]):
    """title + plus + minus + addminus. dismiss(dict | None)."""

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _ReportCustomEditModal { align: center middle; }
    #rc_box {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 95%;
        max-width: 76;
        min-width: 44;
        height: auto;
    }
    """

    def __init__(self, report: str) -> None:
        super().__init__()
        self._report = report

    def compose(self) -> ComposeResult:
        with Container(id="rc_box"):
            yield Label(f"[bold]사용자 정의 행 등록 — {_REPORT_KR[self._report]}[/bold]")
            yield Label("title (행 제목, 필수)")
            yield Input(placeholder="예: 현금성 자산", id="rc-title")
            yield Label("plus (더할 항목, 콤마 구분)")
            yield Input(placeholder="assets_x11, assets_x12", id="rc-plus")
            yield Label("minus (뺄 항목, 콤마 구분 — 없으면 비움)")
            yield Input(placeholder="liabilities_x30", id="rc-minus")
            yield Label("addminus (공식, x=plus−minus 합)")
            yield Input(value="x", id="rc-addminus")
            with Horizontal():
                yield Button("Save (Ctrl+S)", id="rc-save", variant="primary")
                yield Button("Cancel (Esc)", id="rc-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#rc-title", Input).focus()
        except Exception:  # pragma: no cover
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        try:
            title = self.query_one("#rc-title", Input).value.strip()
            plus = _parse_refs(self.query_one("#rc-plus", Input).value)
            minus = _parse_refs(self.query_one("#rc-minus", Input).value)
            addminus = self.query_one("#rc-addminus", Input).value.strip() or "x"
        except AttributeError as e:  # pragma: no cover
            self.notify(f"입력 오류: {e}", severity="error")
            return
        if not title:
            self.notify("title 은 필수입니다.", severity="error")
            return
        if not plus and not minus:
            self.notify("plus 또는 minus 항목이 최소 하나 필요합니다.",
                        severity="error")
            return
        self.dismiss({
            "title": title, "plus": plus, "minus": minus, "addminus": addminus,
        })

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rc-save":
            self.action_save()
        elif event.button.id == "rc-cancel":
            self.action_cancel()


class ReportCustomsScreen(MenuBarMixin, ModalScreen[None]):
    """사용자 정의 보고서 행 list + 추가/삭제 + BS/PL 전환."""

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("t", "toggle_report", "BS/PL", show=True, priority=True),
        *bind_ko("n", "new_row", "New", show=True, priority=True),
        *bind_ko("d", "delete_row", "Delete", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    ReportCustomsScreen { align: center middle; }
    #rc_frame {
        width: 95%;
        max-width: 140;
        min-width: 50;
        height: 90%;
        max-height: 45;
        min-height: 16;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
        layout: vertical;
    }
    #rc_title { height: 1; content-align: center middle; color: $accent; }
    #rc_status { height: auto; padding: 1 0; background: transparent; }
    #rc_status.error { color: $error; }
    #rc_status.warn  { color: $warning; }
    #rc_table { height: 1fr; }
    #rc_foot { height: 1; content-align: center middle; color: $text-muted; }
    """

    @staticmethod
    def _build_menus() -> tuple[MenuSpec, ...]:
        return (
            MenuSpec(name="파일", items=(
                MenuItem("재로드 (r)", "refresh"),
                MenuItem("뒤로 (q)", "back"),
            )),
            MenuSpec(name="행", items=(
                MenuItem("BS/PL 전환 (t)", "toggle_report"),
                MenuItem("새 행 (n)", "new_row"),
                MenuItem("삭제 (d)", "delete_row"),
            )),
        )

    def _menubar_widget_id(self) -> str:
        return "report-customs-menubar"

    def __init__(self, client: WhooingClient, session: SessionState) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self._report = "report_bs"
        self.last_status: str = ""
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="rc_frame"):
            yield Static("[bold]사용자 정의 보고서 행[/bold]", id="rc_title")
            yield MenuBar(self._build_menus(), id="report-customs-menubar")
            yield Static("", id="rc_status")
            yield DataTable(id="rc_table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "t BS/PL · n 신규 · d 삭제 · r 재로드 · Esc/q 닫기",
                id="rc_foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#rc_table", DataTable)
        table.add_columns("id", "title", "plus", "minus", "addminus")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        self.dismiss(None)

    def action_toggle_report(self) -> None:
        idx = _REPORTS.index(self._report)
        self._report = _REPORTS[(idx + 1) % len(_REPORTS)]
        self.action_refresh()

    def action_refresh(self) -> None:
        self._refresh_worker()

    @work(exclusive=True, group="rc-refresh", name="refresh")
    async def _refresh_worker(self) -> None:
        try:
            res = await self._client.call_official_tool("report_customs-list", {
                "section_id": self._session.section_id,
                "report": self._report,
            })
        except ToolError as e:
            self._set_status(f"조회 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as ex:  # pragma: no cover
            self._set_status(f"조회 실패: {ex}", error=True)
            return
        rows = res.get("rows") if isinstance(res, dict) else res
        self._rows = [r for r in (rows or []) if isinstance(r, dict)]
        table = self.query_one("#rc_table", DataTable)
        table.clear()
        for r in self._rows:
            rid = str(r.get("id") or "")
            table.add_row(
                rid,
                str(r.get("title") or "")[:24],
                ", ".join(r.get("plus") or [])[:28],
                ", ".join(r.get("minus") or [])[:20],
                str(r.get("addminus") or "x"),
                key=rid,
            )
        label = _REPORT_KR[self._report]
        if self._rows:
            self._set_status(
                f"{label}: {len(self._rows)} 행 — t 전환 / n 신규 / d 삭제"
            )
        else:
            self._set_status(
                f"{label}: 정의된 행 없음 — n 으로 추가하세요.", warn=True,
            )

    def action_new_row(self) -> None:
        self._new_worker()

    @work(exclusive=True, group="rc-new", name="new")
    async def _new_worker(self) -> None:
        draft = await self.app.push_screen_wait(
            _ReportCustomEditModal(self._report)
        )
        if draft is None:
            self._set_status("등록 취소.")
            return
        try:
            await self._client.call_official_tool("report_customs-create", {
                "section_id": self._session.section_id,
                "report": self._report,
                "title": draft["title"],
                "plus": draft["plus"],
                "minus": draft["minus"],
                "addminus": draft["addminus"],
            })
        except ToolError as e:
            self._set_status(f"등록 실패 [{e.kind}] {e.message}", error=True)
            return
        self._set_status(f"등록 완료 — {draft['title']}")
        self.action_refresh()

    def action_delete_row(self) -> None:
        self._delete_worker()

    @work(exclusive=True, group="rc-delete", name="delete")
    async def _delete_worker(self) -> None:
        table = self.query_one("#rc_table", DataTable)
        if not table.row_count:
            self._set_status("선택할 행이 없습니다.", warn=True)
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            rid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        match = next((r for r in self._rows if str(r.get("id")) == rid), None)
        if not match:
            self._set_status(f"id={rid} 찾을 수 없음.", warn=True)
            return
        ok = await self.app.push_screen_wait(_ConfirmModal(
            f"사용자 정의 행 '{match.get('title') or rid}' (id={rid}) 을(를) "
            f"삭제할까요?\n\n되돌릴 수 없습니다."
        ))
        if not ok:
            self._set_status("삭제 취소.")
            return
        try:
            await self._client.call_official_tool("report_customs-delete", {
                "section_id": self._session.section_id,
                "report": self._report,
                "custom_id": rid,
            })
        except ToolError as e:
            self._set_status(f"삭제 실패 [{e.kind}] {e.message}", error=True)
            return
        self._set_status(f"삭제 완료 — id={rid}")
        self.action_refresh()

    # ---- helpers ---------------------------------------------------------

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#rc_status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
