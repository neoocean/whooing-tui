"""MonthlyEntriesScreen — 매월 입력 (정기/반복) 거래 list + 추가/삭제.

CL #51152+. 후잉의 "매월입력 거래" 기능 — 매달 같은 날 같은 패턴 (월세 /
통신비 / 보험 등) 의 자동 후보. 사용자가 등록해 두면 후잉이 그 날짜에 입력
대상으로 D-Day 표시.

후잉 endpoint 의 정확한 path 가 공식 docs (JS 렌더) 라 확정 안 됨 — 본 화면
은 추정 RESTful endpoint 로 동작. 실 호출 실패 시 ToolError 가 status 에
표시 + caller 가 `client._monthly_collection_path` / `_monthly_path` 만
수정해 즉시 전체 흐름 작동.

키:
  ↑/↓        선택.
  n          신규 등록 — Modal (target_day + 계정 + 금액 + item).
  d          삭제 (Confirm).
  r          새로고침. q / Esc  뒤로.
  F10        메뉴.
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Static,
)

from whooing_tui.client import WhooingClient
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.state import SessionState
from whooing_tui.widgets import (
    MenuBar, MenuBarMixin, MenuItem, MenuSpec, menubar_bindings,
)

log = logging.getLogger(__name__)


def _fmt_money(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


# ---- 보조 modal — 신규 등록 -----------------------------------------------


class _MonthlyEditModal(ModalScreen[dict | None]):
    """target_day + 계정 (l/r) + 금액 + item + memo. dismiss(dict | None)."""

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _MonthlyEditModal {
        align: center middle;
    }
    #me_box {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 95%;
        max-width: 70;
        min-width: 40;
        height: auto;
    }
    #me_grid {
        layout: grid;
        grid-size: 2;
        grid-columns: 16 1fr;
        grid-rows: 1;
    }
    """

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        with Container(id="me_box"):
            yield Label("[bold]매월 입력 거래 등록[/bold]")
            yield Label(
                "[dim](TODO C13: AccountPicker 통합 — 현재 raw account_id 텍스트 입력)[/dim]",
            )
            yield Label("target_day (1~31)")
            yield Input(placeholder="25", id="me-day")
            yield Label("money (KRW)")
            yield Input(placeholder="50000", id="me-money")
            yield Label("l_account_id (차변)")
            yield Input(placeholder="x20  (예: 식비)", id="me-l-id")
            yield Label("l_account (type)")
            yield Input(placeholder="expenses", id="me-l-type")
            yield Label("r_account_id (대변)")
            yield Input(placeholder="x11  (예: 현금)", id="me-r-id")
            yield Label("r_account (type)")
            yield Input(placeholder="assets", id="me-r-type")
            yield Label("item")
            yield Input(placeholder="월세 / 통신비 등", id="me-item")
            yield Label("memo")
            yield Input(placeholder="(optional)", id="me-memo")
            with Horizontal():
                yield Button("Save (Ctrl+S)", id="me-save", variant="primary")
                yield Button("Cancel (Esc)", id="me-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#me-day", Input).focus()
        except Exception:  # pragma: no cover
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        try:
            day = int(self.query_one("#me-day", Input).value.strip())
            money = int(self.query_one("#me-money", Input).value.replace(",", "").strip())
            l_id = self.query_one("#me-l-id", Input).value.strip()
            l_type = self.query_one("#me-l-type", Input).value.strip()
            r_id = self.query_one("#me-r-id", Input).value.strip()
            r_type = self.query_one("#me-r-type", Input).value.strip()
            item = self.query_one("#me-item", Input).value.strip()
            memo = self.query_one("#me-memo", Input).value.strip()
        except (ValueError, AttributeError) as e:
            self.notify(f"입력 오류: {e}", severity="error")
            return
        if not (1 <= day <= 31):
            self.notify("target_day 는 1~31 사이여야 합니다.", severity="error")
            return
        if not (l_id and l_type and r_id and r_type and money > 0):
            self.notify("계정 + 금액 모두 필수.", severity="error")
            return
        self.dismiss({
            "target_day": day, "money": money,
            "l_account": l_type, "l_account_id": l_id,
            "r_account": r_type, "r_account_id": r_id,
            "item": item, "memo": memo,
        })

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "me-save":
            self.action_save()
        else:
            self.action_cancel()


# CL #51156+ (review C6): `_ConfirmModal` → `widgets.ConfirmModal` 사용.

from whooing_tui.widgets import ConfirmModal as _ConfirmModal  # 호환 alias.


# ---- Main screen --------------------------------------------------------


class MonthlyEntriesScreen(MenuBarMixin, Screen):
    """매월 입력 거래 list + 추가/삭제."""

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("n", "new_entry", "New", show=True, priority=True),
        *bind_ko("d", "delete_entry", "Delete", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    MonthlyEntriesScreen { layout: vertical; }
    #m_status {
        height: auto;
        padding: 1;
        background: $boost;
    }
    #m_status.error { color: $error; }
    #m_status.warn  { color: $warning; }
    """

    @staticmethod
    def _build_menus() -> tuple[MenuSpec, ...]:
        return (
            MenuSpec(
                name="파일",
                items=(
                    MenuItem("재로드 (r)", "refresh"),
                    MenuItem("뒤로 (q)", "back"),
                ),
            ),
            MenuSpec(
                name="입력",
                items=(
                    MenuItem("새 매월거래 (n)", "new_entry"),
                ),
            ),
        )

    def _menubar_widget_id(self) -> str:
        return "monthly-menubar"

    def __init__(self, client: WhooingClient, session: SessionState) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self.last_status: str = ""
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield MenuBar(self._build_menus(), id="monthly-menubar")
        yield Static("", id="m_status")
        yield Vertical(
            DataTable(id="m_table", zebra_stripes=True, cursor_type="row"),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#m_table", DataTable)
        table.add_columns("id", "day", "left", "right", "money", "item")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._refresh_worker()

    @work(exclusive=True, group="monthly", name="refresh")
    async def _refresh_worker(self) -> None:
        try:
            rows = await self._client.list_monthly(
                section_id=self._session.section_id,
            )
        except ToolError as e:
            self._set_status(
                f"매월거래 조회 실패 [{e.kind}] {e.message} — "
                f"endpoint 추정 mismatch 가능. client.py 의 "
                f"_monthly_collection_path 확인.",
                error=True,
            )
            return
        except Exception as ex:  # pragma: no cover
            self._set_status(f"매월거래 조회 실패: {ex}", error=True)
            return
        self._rows = list(rows or [])
        table = self.query_one("#m_table", DataTable)
        table.clear()
        for r in self._rows:
            l_id = str(r.get("l_account_id") or "")
            r_id = str(r.get("r_account_id") or "")
            l_name = self._session.title_of(l_id) if l_id else ""
            r_name = self._session.title_of(r_id) if r_id else ""
            table.add_row(
                str(r.get("monthly_id") or r.get("id") or ""),
                str(r.get("target_day") or ""),
                l_name or l_id,
                r_name or r_id,
                _fmt_money(r.get("money")),
                str(r.get("item") or "")[:30],
                key=str(r.get("monthly_id") or r.get("id") or ""),
            )
        self._set_status(
            f"{len(self._rows)} 건 — n=신규 / d=삭제 / r=새로고침 / q=뒤로"
        )

    def action_new_entry(self) -> None:
        self._new_worker()

    @work(exclusive=True, group="monthly", name="new")
    async def _new_worker(self) -> None:
        draft = await self.app.push_screen_wait(_MonthlyEditModal(self._session))
        if draft is None:
            self._set_status("등록 취소.")
            return
        try:
            await self._client.create_monthly(
                section_id=self._session.section_id, **draft,
            )
        except ToolError as e:
            self._set_status(
                f"등록 실패 [{e.kind}] {e.message} — endpoint 추정 mismatch 가능.",
                error=True,
            )
            return
        self._set_status(
            f"등록 완료 — target_day={draft['target_day']} "
            f"item={draft['item'] or '(empty)'}"
        )
        self.action_refresh()

    def action_delete_entry(self) -> None:
        self._delete_worker()

    @work(exclusive=True, group="monthly", name="delete")
    async def _delete_worker(self) -> None:
        table = self.query_one("#m_table", DataTable)
        if not table.row_count:
            self._set_status("선택할 매월거래가 없습니다.", warn=True)
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            mid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        match = next((r for r in self._rows if str(
            r.get("monthly_id") or r.get("id") or "") == mid), None)
        if not match:
            self._set_status(f"id={mid} 찾을 수 없음.", warn=True)
            return
        ok = await self.app.push_screen_wait(_ConfirmModal(
            f"매월거래 {mid} (day={match.get('target_day')}, "
            f"item={match.get('item') or ''}) 를 삭제할까요?\n\n"
            f"되돌릴 수 없습니다."
        ))
        if not ok:
            self._set_status("삭제 취소.")
            return
        try:
            await self._client.delete_monthly(
                section_id=self._session.section_id, monthly_id=mid,
            )
        except ToolError as e:
            self._set_status(
                f"삭제 실패 [{e.kind}] {e.message} — endpoint 추정 mismatch 가능.",
                error=True,
            )
            return
        self._set_status(f"삭제 완료 — id={mid}")
        self.action_refresh()

    # ---- helpers ---------------------------------------------------------

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#m_status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
