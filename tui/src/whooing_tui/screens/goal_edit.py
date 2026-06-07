"""GoalEditScreen — 장기목표 + 월별 자본 목표값 편집.

CL #51154+. 후잉의 두 종류 goal:
  - **장기목표** (`/budget_goal.json`): 단일 amount + target_date.
  - **월별 자본 목표** (`/goal.json`): YYYYMM 별 자본 amount.

setter endpoint 는 추정 (POST 같은 path) — 라이브 검증 시 mismatch 면 status.

키:
  Tab        장기목표 ↔ 월별 자본 보기 전환.
  e/Enter    선택 row 편집 (modal — amount 입력).
  r          새로고침.
  q / Esc    뒤로.
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


# ---- 보조 modal — 목표 편집 (amount + 옵션 date/month) -------------------


class _GoalEditModal(ModalScreen[dict | None]):
    """단일 amount + (target_date 또는 target_month) 편집.

    `mode='budget_goal'` → date 1줄, `mode='goal'` → month (YYYYMM) 1줄.
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _GoalEditModal { align: center middle; }
    #ge_box { background: $panel; border: thick $primary; padding: 1; max-width: 60; }
    """

    def __init__(
        self, *, mode: str, title: str,
        initial_amount: int = 0, initial_extra: str = "",
    ) -> None:
        super().__init__()
        self._mode = mode  # 'budget_goal' | 'goal'
        self._title = title
        self._initial_amount = initial_amount
        self._initial_extra = initial_extra

    def compose(self) -> ComposeResult:
        with Container(id="ge_box"):
            yield Label(f"[bold]{self._title}[/bold]")
            yield Label("amount (KRW)")
            yield Input(
                value=str(self._initial_amount) if self._initial_amount else "",
                placeholder="100000000",
                id="ge-amount",
            )
            if self._mode == "budget_goal":
                yield Label("target_date (YYYYMMDD, 옵션)")
                yield Input(
                    value=self._initial_extra,
                    placeholder="20301231",
                    id="ge-extra",
                )
            else:
                yield Label("target_month (YYYYMM)")
                yield Input(
                    value=self._initial_extra,
                    placeholder="202612",
                    id="ge-extra",
                )
            with Horizontal():
                yield Button("Save (Ctrl+S)", id="ge-ok", variant="primary")
                yield Button("Cancel (Esc)", id="ge-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#ge-amount", Input).focus()
        except Exception:  # pragma: no cover
            pass

    def action_save(self) -> None:
        try:
            raw = self.query_one("#ge-amount", Input).value.replace(",", "").strip()
            amount = int(raw) if raw else 0
        except (ValueError, AttributeError):
            self.notify("amount 는 정수.", severity="error")
            return
        try:
            extra = self.query_one("#ge-extra", Input).value.strip()
        except Exception:  # pragma: no cover
            extra = ""
        self.dismiss({
            "amount": amount,
            "extra": extra or None,
        })

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ge-ok":
            self.action_save()
        else:
            self.action_cancel()


# ---- Main screen --------------------------------------------------------


class GoalEditScreen(MenuBarMixin, ModalScreen[None]):
    """장기목표 + 월별 자본 목표 편집.

    CL #52899+: 사용자 요청 — Screen → ModalScreen 으로 popup 화.
    """

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        Binding("enter", "edit", "Edit", show=True, priority=True),
        *bind_ko("e", "edit", "", show=False, priority=True),
        Binding("tab", "toggle_mode", "장기↔월별",
                show=True, priority=True),
    ]

    DEFAULT_CSS = """
    GoalEditScreen { align: center middle; }
    #g-frame {
        width: 95%;
        max-width: 120;
        min-width: 50;
        height: 90%;
        max-height: 40;
        min-height: 16;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
        layout: vertical;
    }
    #g-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #g_status {
        height: auto;
        padding: 1 0;
        background: transparent;
    }
    #g_status.error { color: $error; }
    #g_status.warn  { color: $warning; }
    #g_table { height: 1fr; }
    #g-foot {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
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
                name="편집",
                items=(
                    MenuItem("편집 (Enter)", "edit"),
                    MenuItem("장기↔월별 전환 (Tab)", "toggle_mode"),
                ),
            ),
        )

    def _menubar_widget_id(self) -> str:
        return "goal-menubar"

    def __init__(self, client: WhooingClient, session: SessionState) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self.last_status: str = ""
        self._mode: str = "budget_goal"  # 'budget_goal' | 'goal'.
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        # CL #52899+: ModalScreen popup — Header/Footer 위젯 제거.
        with Vertical(id="g-frame"):
            yield Static("[bold]목표 편집[/bold]", id="g-title")
            yield MenuBar(self._build_menus(), id="goal-menubar")
            yield Static("", id="g_status")
            yield DataTable(id="g_table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "Enter 편집 · Tab 장기↔월별 · r 재로드 · Esc/q 닫기",
                id="g-foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#g_table", DataTable)
        table.add_columns("when", "amount", "note")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        # CL #52899+: ModalScreen 표준 종료 path.
        self.dismiss(None)

    def action_toggle_mode(self) -> None:
        self._mode = "goal" if self._mode == "budget_goal" else "budget_goal"
        self.action_refresh()

    def action_refresh(self) -> None:
        self._refresh_worker()

    @work(exclusive=True, group="goal", name="refresh")
    async def _refresh_worker(self) -> None:
        try:
            if self._mode == "budget_goal":
                data = await self._client.get_budget_goal(
                    section_id=self._session.section_id,
                )
                # 단일 dict — table 1 row.
                self._rows = [data] if data else []
            else:
                data = await self._client.get_goal(
                    section_id=self._session.section_id,
                )
                rows: list[dict[str, Any]] = []
                if isinstance(data, dict):
                    r = data.get("rows") or data.get("items") or []
                    if isinstance(r, list):
                        rows = [d for d in r if isinstance(d, dict)]
                elif isinstance(data, list):
                    rows = [d for d in data if isinstance(d, dict)]
                self._rows = rows
        except ToolError as e:
            self._set_status(
                f"목표 조회 실패 [{e.kind}] {e.message}", error=True,
            )
            return
        table = self.query_one("#g_table", DataTable)
        table.clear()
        for r in self._rows:
            if self._mode == "budget_goal":
                when = str(r.get("target_date") or "(미설정)")
            else:
                when = str(r.get("target_month") or r.get("month") or "")
            amount = _fmt_money(r.get("amount") or r.get("goal"))
            note = str(r.get("note") or "")
            table.add_row(when, amount, note, key=when or "row")
        mode_label = "장기목표" if self._mode == "budget_goal" else "월별 자본 목표"
        self._set_status(
            f"[{mode_label}] {len(self._rows)} 건. "
            f"Enter=편집 / Tab=전환 / r=새로고침"
        )

    def action_edit(self) -> None:
        self._edit_worker()

    @work(exclusive=True, group="goal", name="edit")
    async def _edit_worker(self) -> None:
        # 현재 선택 row.
        table = self.query_one("#g_table", DataTable)
        match: dict[str, Any] = {}
        if table.row_count:
            try:
                row_key = table.coordinate_to_cell_key(
                    table.cursor_coordinate,
                ).row_key
                key = str(row_key.value)
                match = next(
                    (r for r in self._rows if (
                        str(r.get("target_date") or r.get("target_month") or "row") == key
                    )), {},
                )
            except (AttributeError, TypeError, ValueError):
                pass
        # mode 별 mode + initial 채움.
        if self._mode == "budget_goal":
            initial_amount = int(match.get("amount") or 0)
            initial_extra = str(match.get("target_date") or "")
            modal_title = "장기목표 편집"
        else:
            initial_amount = int(match.get("amount") or match.get("goal") or 0)
            initial_extra = str(match.get("target_month") or match.get("month") or "")
            modal_title = "월별 자본 목표 편집"
        result = await self.app.push_screen_wait(_GoalEditModal(
            mode=self._mode, title=modal_title,
            initial_amount=initial_amount, initial_extra=initial_extra,
        ))
        if result is None:
            self._set_status("편집 취소.")
            return
        try:
            if self._mode == "budget_goal":
                await self._client.set_budget_goal(
                    section_id=self._session.section_id,
                    amount=result["amount"],
                    target_date=result.get("extra"),
                )
            else:
                # month 필수.
                month = result.get("extra") or ""
                if not (len(month) == 6 and month.isdigit()):
                    self._set_status(
                        "target_month 는 YYYYMM 6자리 필요.", error=True,
                    )
                    return
                await self._client.set_goal(
                    section_id=self._session.section_id,
                    target_month=month, amount=result["amount"],
                )
        except ToolError as e:
            self._set_status(
                f"목표 갱신 실패 [{e.kind}] {e.message} — endpoint 추정 mismatch 가능.",
                error=True,
            )
            return
        self._set_status("저장 완료.")
        self.action_refresh()

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#g_status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
