"""FrequentItemsScreen — 자주입력 거래 list + 추가/삭제 + 1-탭 사용.

0.84.0 (로드맵 P1-B). 후잉의 "자주입력 거래" — 커피 / 점심 / 교통처럼 자주
반복되는 거래 패턴을 슬롯(slot1~slot3)에 등록해 두고 한 번에 입력한다.
매월입력(`MonthlyEntriesScreen`)이 *날짜 기반* 반복이라면 이쪽은 *빈도 기반*
빠른 입력 — 둘이 대칭.

키:
  ↑/↓        선택.
  Enter      선택한 자주입력을 *새 거래로 사용* — 값이 채워진 입력 폼 오픈.
  n          신규 등록 — Modal (slot + 계정 + 금액 + item).
  d          삭제 (Confirm).
  r          새로고침. q / Esc  뒤로.
  F10        메뉴.

dismiss 결과: Enter 로 사용 선택 시 그 item dict (entry_id 없음 → 새 거래
prefill), 그 외엔 None.
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
from whooing_tui.screens.account_picker import AccountPickerScreen
from whooing_tui.screens.edit_entry import _AccountButton
from whooing_tui.state import SessionState
from whooing_tui.widgets import (
    ConfirmModal as _ConfirmModal,
    MenuBar, MenuBarMixin, MenuItem, MenuSpec, menubar_bindings,
)

log = logging.getLogger(__name__)

# 후잉 자주입력 슬롯 — slot1~slot3.
_SLOTS = ("slot1", "slot2", "slot3")


def _fmt_money(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


# ---- 보조 modal — 신규 등록 -----------------------------------------------


class _FrequentEditModal(ModalScreen[dict | None]):
    """slot + 계정(l/r) + 금액(선택) + item. dismiss(dict | None)."""

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _FrequentEditModal { align: center middle; }
    #fe_box {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 95%;
        max-width: 70;
        min-width: 40;
        height: auto;
    }
    """

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        with Container(id="fe_box"):
            yield Label("[bold]자주입력 거래 등록[/bold]")
            yield Label("slot (slot1~slot3)")
            yield Input(value="slot1", id="fe-slot")
            yield Label("money (KRW) — 선택")
            yield Input(placeholder="4500", id="fe-money")
            yield Label("차변 (left) — Enter 로 선택")
            yield _AccountButton(button_id="fe-left")
            yield Label("대변 (right) — Enter 로 선택")
            yield _AccountButton(button_id="fe-right")
            yield Label("item (필수)")
            yield Input(placeholder="커피 / 점심 / 교통 등", id="fe-item")
            with Horizontal():
                yield Button("Save (Ctrl+S)", id="fe-save", variant="primary")
                yield Button("Cancel (Esc)", id="fe-cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#fe-item", Input).focus()
        except Exception:  # pragma: no cover
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        try:
            slot = self.query_one("#fe-slot", Input).value.strip() or "slot1"
            money_raw = self.query_one("#fe-money", Input).value.replace(",", "").strip()
            left = self.query_one("#fe-left", _AccountButton)
            right = self.query_one("#fe-right", _AccountButton)
            item = self.query_one("#fe-item", Input).value.strip()
        except AttributeError as e:  # pragma: no cover
            self.notify(f"입력 오류: {e}", severity="error")
            return
        if slot not in _SLOTS:
            self.notify("slot 은 slot1 / slot2 / slot3 중 하나.", severity="error")
            return
        if not item:
            self.notify("item 은 필수입니다.", severity="error")
            return
        if not (left.account_id and right.account_id):
            self.notify("차변·대변 계정 모두 필수.", severity="error")
            return
        money: int | None = None
        if money_raw:
            try:
                money = int(money_raw)
            except ValueError:
                self.notify("money 는 숫자만.", severity="error")
                return
        self.dismiss({
            "slot": slot, "item": item, "money": money,
            "l_account": left.type_key, "l_account_id": left.account_id,
            "r_account": right.type_key, "r_account_id": right.account_id,
        })

    def _open_account_picker(self, button_id: str) -> None:
        side = "left" if button_id == "fe-left" else "right"
        btn = self.query_one(f"#{button_id}", _AccountButton)

        def _on_pick(result: tuple[str, str, str] | None) -> None:
            if result is None:
                return
            aid, title, type_key = result
            btn.set_account(aid, title, type_key)

        self.app.push_screen(
            AccountPickerScreen(
                self._session, side=side, current_id=btn.account_id or None,
            ),
            _on_pick,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "fe-save":
            self.action_save()
        elif bid == "fe-cancel":
            self.action_cancel()
        elif bid in ("fe-left", "fe-right"):
            self._open_account_picker(bid)


# ---- Main screen --------------------------------------------------------


class FrequentItemsScreen(MenuBarMixin, ModalScreen[dict | None]):
    """자주입력 거래 list + 추가/삭제 + Enter 로 새 거래 사용."""

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("n", "new_item", "New", show=True, priority=True),
        *bind_ko("d", "delete_item", "Delete", show=True, priority=True),
        Binding("enter", "use_item", "사용", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    FrequentItemsScreen { align: center middle; }
    #f_frame {
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
    #f_title { height: 1; content-align: center middle; color: $accent; }
    #f_status { height: auto; padding: 1 0; background: transparent; }
    #f_status.error { color: $error; }
    #f_status.warn  { color: $warning; }
    #f_table { height: 1fr; }
    #f_foot { height: 1; content-align: center middle; color: $text-muted; }
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
                    MenuItem("선택을 새 거래로 사용 (Enter)", "use_item"),
                    MenuItem("새 자주입력 (n)", "new_item"),
                    MenuItem("삭제 (d)", "delete_item"),
                ),
            ),
        )

    def _menubar_widget_id(self) -> str:
        return "frequent-menubar"

    def __init__(self, client: WhooingClient, session: SessionState) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self.last_status: str = ""
        self._rows: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="f_frame"):
            yield Static("[bold]자주입력 거래[/bold]", id="f_title")
            yield MenuBar(self._build_menus(), id="frequent-menubar")
            yield Static("", id="f_status")
            yield DataTable(id="f_table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "Enter 새 거래로 사용 · n 신규 · d 삭제 · r 재로드 · Esc/q 닫기",
                id="f_foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#f_table", DataTable)
        table.add_columns("id", "slot", "left", "right", "money", "item")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh_worker()

    @work(exclusive=True, group="frequent-refresh", name="refresh")
    async def _refresh_worker(self) -> None:
        try:
            rows = await self._client.list_frequent(
                section_id=self._session.section_id,
            )
        except ToolError as e:
            self._set_status(
                f"자주입력 조회 실패 [{e.kind}] {e.message}", error=True,
            )
            return
        except Exception as ex:  # pragma: no cover
            self._set_status(f"자주입력 조회 실패: {ex}", error=True)
            return
        self._rows = list(rows or [])
        table = self.query_one("#f_table", DataTable)
        table.clear()
        for r in self._rows:
            fid = str(r.get("item_id") or r.get("id") or "")
            l_id = str(r.get("l_account_id") or "")
            r_id = str(r.get("r_account_id") or "")
            l_name = self._session.title_of(l_id) if l_id else ""
            r_name = self._session.title_of(r_id) if r_id else ""
            table.add_row(
                fid,
                str(r.get("slot") or ""),
                l_name or l_id,
                r_name or r_id,
                _fmt_money(r.get("money")),
                str(r.get("item") or "")[:30],
                key=fid,
            )
        if self._rows:
            self._set_status(
                f"{len(self._rows)} 건 — Enter 사용 / n 신규 / d 삭제 / q 뒤로"
            )
        else:
            self._set_status(
                "자주입력 거래가 없습니다 — n 으로 등록하세요.", warn=True,
            )

    def _cursor_row(self) -> dict[str, Any] | None:
        table = self.query_one("#f_table", DataTable)
        if not table.row_count:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            fid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return None
        return next(
            (r for r in self._rows
             if str(r.get("item_id") or r.get("id") or "") == fid), None,
        )

    def action_use_item(self) -> None:
        """선택한 자주입력을 새 거래 prefill 로 반환 (entry_id 없음)."""
        row = self._cursor_row()
        if row is None:
            self._set_status("선택할 자주입력이 없습니다.", warn=True)
            return
        # entry_id 없는 dict → EntryEditDialog 가 '새 거래(값 채움)' 로 연다.
        draft = {
            k: row.get(k) for k in (
                "item", "money", "l_account", "l_account_id",
                "r_account", "r_account_id",
            )
        }
        self.dismiss(draft)

    def action_new_item(self) -> None:
        self._new_worker()

    @work(exclusive=True, group="frequent-new", name="new")
    async def _new_worker(self) -> None:
        draft = await self.app.push_screen_wait(_FrequentEditModal(self._session))
        if draft is None:
            self._set_status("등록 취소.")
            return
        try:
            await self._client.create_frequent(
                section_id=self._session.section_id, **draft,
            )
        except ToolError as e:
            self._set_status(f"등록 실패 [{e.kind}] {e.message}", error=True)
            return
        self._set_status(
            f"등록 완료 — slot={draft['slot']} item={draft['item']}"
        )
        self.action_refresh()

    def action_delete_item(self) -> None:
        self._delete_worker()

    @work(exclusive=True, group="frequent-delete", name="delete")
    async def _delete_worker(self) -> None:
        row = self._cursor_row()
        if row is None:
            self._set_status("선택할 자주입력이 없습니다.", warn=True)
            return
        fid = str(row.get("item_id") or row.get("id") or "")
        slot = str(row.get("slot") or "slot1")
        ok = await self.app.push_screen_wait(_ConfirmModal(
            f"자주입력 {fid} ({slot}, item={row.get('item') or ''}) 를 "
            f"삭제할까요?\n\n되돌릴 수 없습니다."
        ))
        if not ok:
            self._set_status("삭제 취소.")
            return
        try:
            await self._client.delete_frequent(
                section_id=self._session.section_id, slot=slot, item_id=fid,
            )
        except ToolError as e:
            self._set_status(f"삭제 실패 [{e.kind}] {e.message}", error=True)
            return
        self._set_status(f"삭제 완료 — id={fid}")
        self.action_refresh()

    # ---- helpers ---------------------------------------------------------

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#f_status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
