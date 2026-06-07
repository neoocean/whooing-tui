"""BudgetEditScreen — 예산 입력/관리 (지출 / 수입 항목별 amount).

CL #51153+. 후잉 budget endpoint 의 *조회* 는 알려져 있고 (`/budget/<account>.json`
GET), *setter* 는 같은 path POST 로 추정. 라이브 검증 시 실패하면 status
에 디버깅 안내.

키:
  ↑/↓        선택.
  e / Enter  예산 amount 편집 (modal — 단일 숫자 입력).
  d          예산 제거.
  r          새로고침.
  Tab / [/]  expenses ↔ income 전환.
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


# ---- 보조 modal — 금액 입력 -----------------------------------------------


class _AmountModal(ModalScreen[int | None]):
    """양의 정수 입력. 빈 값 = 0 (예산 제거 신호 — caller 가 분기)."""

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _AmountModal { align: center middle; }
    #am_box { background: $panel; border: thick $primary; padding: 1; max-width: 50; }
    """

    def __init__(self, *, title: str, initial: int = 0) -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Container(id="am_box"):
            yield Label(f"[bold]{self._title}[/bold]")
            yield Input(
                value=str(self._initial) if self._initial else "",
                placeholder="50000",
                id="am-input",
            )
            with Horizontal():
                yield Button("Save", id="am-ok", variant="primary")
                yield Button("Cancel", id="am-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#am-input", Input).focus()
        except Exception:  # pragma: no cover
            pass

    def action_save(self) -> None:
        raw = self.query_one("#am-input", Input).value.replace(",", "").strip()
        if not raw:
            self.dismiss(0)
            return
        try:
            n = int(raw)
        except ValueError:
            self.notify("정수만 입력.", severity="error")
            return
        if n < 0:
            self.notify("음수 불가.", severity="error")
            return
        self.dismiss(n)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "am-ok":
            self.action_save()
        else:
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()


# ---- Main screen --------------------------------------------------------


class BudgetEditScreen(MenuBarMixin, ModalScreen[None]):
    """예산 list (account 별) + 편집/삭제. account = expenses | income 토글.

    CL #52899+: 사용자 요청 — Screen → ModalScreen 으로 popup 화.
    """

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        Binding("enter", "edit_amount", "Edit", show=True, priority=True),
        *bind_ko("e", "edit_amount", "", show=False, priority=True),
        *bind_ko("d", "delete_budget", "Delete", show=True, priority=True),
        Binding("tab", "toggle_account", "expenses↔income",
                show=True, priority=True),
        Binding("right_square_bracket", "toggle_account", "", show=False),
        Binding("left_square_bracket", "toggle_account", "", show=False),
    ]

    DEFAULT_CSS = """
    BudgetEditScreen { align: center middle; }
    #b-frame {
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
    #b-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #b_status {
        height: auto;
        padding: 1 0;
        background: transparent;
    }
    #b_status.error { color: $error; }
    #b_status.warn  { color: $warning; }
    #b_table { height: 1fr; }
    #b-foot {
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
                    MenuItem("금액 편집 (Enter)", "edit_amount"),
                    MenuItem("예산 제거 (d)", "delete_budget"),
                    MenuItem("expenses ↔ income (Tab)", "toggle_account"),
                ),
            ),
        )

    def _menubar_widget_id(self) -> str:
        return "budget-menubar"

    def __init__(self, client: WhooingClient, session: SessionState) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self.last_status: str = ""
        self._account: str = "expenses"  # 토글 — expenses | income.
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        # CL #52899+: ModalScreen popup — Header/Footer 위젯 제거.
        with Vertical(id="b-frame"):
            yield Static("[bold]예산 편집[/bold]", id="b-title")
            yield MenuBar(self._build_menus(), id="budget-menubar")
            yield Static("", id="b_status")
            yield DataTable(id="b_table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "Enter 금액편집 · d 제거 · Tab expenses↔income · r 재로드 · Esc/q 닫기",
                id="b-foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#b_table", DataTable)
        table.add_columns("account_id", "title", "budget", "actual")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        # CL #52899+: ModalScreen 표준 종료 path.
        self.dismiss(None)

    def action_toggle_account(self) -> None:
        self._account = "income" if self._account == "expenses" else "expenses"
        self.action_refresh()

    def action_refresh(self) -> None:
        self._refresh_worker()

    @work(exclusive=True, group="budget", name="refresh")
    async def _refresh_worker(self) -> None:
        try:
            data = await self._client.get_budget(
                section_id=self._session.section_id, account=self._account,
            )
        except ToolError as e:
            self._set_status(
                f"예산 조회 실패 [{e.kind}] {e.message}", error=True,
            )
            return
        # 후잉 응답 형식 — `{rows: [...]}` 또는 dict-of-rows. `_normalize` 패턴.
        rows: list[dict[str, Any]] = []
        if isinstance(data, dict):
            r = data.get("rows") or data.get("items") or []
            if isinstance(r, list):
                rows = [d for d in r if isinstance(d, dict)]
        elif isinstance(data, list):
            rows = [d for d in data if isinstance(d, dict)]
        self._rows = rows
        table = self.query_one("#b_table", DataTable)
        table.clear()
        for r in self._rows:
            aid = str(r.get("account_id") or "")
            title = self._session.title_of(aid) if aid else ""
            table.add_row(
                aid,
                title or str(r.get("title") or ""),
                _fmt_money(r.get("budget") or r.get("amount")),
                _fmt_money(r.get("actual") or r.get("used")),
                key=aid,
            )
        self._set_status(
            f"[{self._account}] {len(self._rows)} 건. "
            f"Enter=편집 / d=삭제 / Tab=전환 / r=새로고침"
        )

    def action_edit_amount(self) -> None:
        self._edit_worker()

    @work(exclusive=True, group="budget", name="edit")
    async def _edit_worker(self) -> None:
        table = self.query_one("#b_table", DataTable)
        if not table.row_count:
            self._set_status("선택할 항목이 없습니다.", warn=True)
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            aid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        match = next((r for r in self._rows if str(r.get("account_id")) == aid), None)
        if not match:
            return
        current = int(match.get("budget") or match.get("amount") or 0)
        title = self._session.title_of(aid) if aid else aid
        new_amount = await self.app.push_screen_wait(_AmountModal(
            title=f"{title} ({aid}) 예산", initial=current,
        ))
        if new_amount is None:
            self._set_status("편집 취소.")
            return
        try:
            if new_amount == 0:
                # 0 = 제거 의도.
                await self._client.delete_budget(
                    section_id=self._session.section_id,
                    account=self._account, account_id=aid,
                )
                self._set_status(f"예산 제거: {title} ({aid})")
            else:
                await self._client.set_budget(
                    section_id=self._session.section_id,
                    account=self._account, account_id=aid,
                    amount=new_amount,
                )
                self._set_status(
                    f"예산 갱신: {title} ({aid}) = {_fmt_money(new_amount)}"
                )
        except ToolError as e:
            self._set_status(
                f"예산 갱신 실패 [{e.kind}] {e.message} — endpoint 추정 mismatch 가능.",
                error=True,
            )
            return
        self.action_refresh()

    def action_delete_budget(self) -> None:
        self._delete_worker()

    @work(exclusive=True, group="budget", name="delete")
    async def _delete_worker(self) -> None:
        table = self.query_one("#b_table", DataTable)
        if not table.row_count:
            self._set_status("선택할 항목이 없습니다.", warn=True)
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            aid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return
        try:
            await self._client.delete_budget(
                section_id=self._session.section_id,
                account=self._account, account_id=aid,
            )
        except ToolError as e:
            self._set_status(
                f"제거 실패 [{e.kind}] {e.message}", error=True,
            )
            return
        self._set_status(f"예산 제거: {aid}")
        self.action_refresh()

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#b_status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
