"""외부입력(임시저장소) 조회 · 확정(입력) · 삭제 화면.

후잉 임시저장소(카드/은행 SMS 가 파싱됐지만 미확정인 항목)를 TUI 에서 다룬다.
지금까지 후잉 웹 UI(단축키 `o`)에서만 가능하던 일을 같은 흐름으로:

  - 조회: 진입 시 `OutsideClient.list_all` 로 전체 적재(대기 건수=목록 길이).
  - 입력(확정): 행 선택 → AccountPicker 로 차변(지출) 계정 지정 →
    `OutsideClient.confirm` 가 거래 생성 + 해당 out_id 제거(원자적).
  - 삭제: `OutsideClient.delete` 로 장부 입력 없이 임시저장소에서 제거.
  - 전체 비우기: `OutsideClient.empty`.

자세한 배경/엔드포인트는 [`docs/scenarios/14-external-input-staging.md`].
dismiss 값: True = 한 건이라도 확정/삭제했음, False = 변경 없이 닫음.
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from whooing_tui.ime import bind_ko
from whooing_tui.outside import OutsideClient, build_entry, parse_counter_account
from whooing_tui.state import SessionState
from whooing_tui.text_utils import fmt_money as _fmt_money

log = logging.getLogger(__name__)


def _money_text(money: Any) -> str:
    """임시저장소 금액 표시 — 정수형은 콤마, 외화 소수는 그대로."""
    try:
        if isinstance(money, bool):  # pragma: no cover — 방어
            return str(money)
        if isinstance(money, int) or (isinstance(money, float) and money.is_integer()):
            return _fmt_money(int(money))
        if isinstance(money, float):
            return f"{money:g}"
        return str(money)
    except Exception:  # pragma: no cover
        return str(money)


def _account_title(session: SessionState, account_id: str) -> str:
    if not account_id:
        return ""
    try:
        title = session.title_of(account_id)
        if title:
            return str(title)
    except Exception:
        pass
    return account_id


class OutsideInboxScreen(ModalScreen[bool]):
    """외부입력(임시저장소) 목록 + 확정/삭제."""

    DEFAULT_CSS = """
    OutsideInboxScreen {
        align: center middle;
    }
    #out-frame {
        width: 95%;
        max-width: 150;
        min-width: 60;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #out-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #out-summary {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #out-hint {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #out-table {
        height: auto;
        max-height: 18;
        margin-top: 1;
    }
    #out-status {
        height: auto;
        min-height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #out-status.error { color: $error; }
    #out-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #out-buttons Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "close", "닫기", show=True),
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        Binding("enter", "confirm", "입력", show=True),
        Binding("c", "confirm", "입력 (c)", show=True),
        *bind_ko("c", "confirm", "입력"),
        Binding("d", "delete", "삭제 (d)", show=True),
        *bind_ko("d", "delete", "삭제"),
        Binding("e", "empty", "전체비우기 (e)", show=True),
        *bind_ko("e", "empty", "전체비우기"),
        Binding("f5", "refresh", "새로고침 (F5)", show=True, priority=True),
        Binding("ctrl+r", "refresh", "새로고침", show=False, priority=True),
    ]

    def __init__(
        self,
        outside: OutsideClient,
        *,
        section_id: str,
        session: SessionState,
    ) -> None:
        super().__init__()
        self._outside = outside
        self._section_id = section_id
        self._session = session
        self._rows: list[dict[str, Any]] = []
        self._dirty = False
        self._busy = False
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="out-frame"):
            yield Static("[bold]📥 외부입력 (임시저장소)[/bold]", id="out-title")
            yield Static("", id="out-summary")
            yield Static(
                "↑/↓ 이동 · Enter/c: 입력(확정) · d: 삭제 · e: 전체비우기 · "
                "F5: 새로고침 · Esc: 닫기",
                id="out-hint",
            )
            yield DataTable(id="out-table", cursor_type="row")
            yield Static("", id="out-status")
            with Horizontal(id="out-buttons"):
                yield Button("입력 (c)", id="out-btn-confirm", variant="success")
                yield Button("삭제 (d)", id="out-btn-delete", variant="error")
                yield Button("전체비우기 (e)", id="out-btn-empty")
                yield Button("새로고침 (F5)", id="out-btn-refresh")
                yield Button("닫기 (Esc)", id="out-btn-close")

    def on_mount(self) -> None:
        self._set_status("🌐 임시저장소 불러오는 중…")
        self._load_worker()

    # ---- rendering ------------------------------------------------------

    def _render_summary(self) -> None:
        try:
            self.query_one("#out-summary", Static).update(
                f"섹션 [b]{self._section_id}[/b] · 대기 [b]{len(self._rows)}[/b] 건"
            )
        except Exception:
            pass

    def _render_table(self) -> None:
        try:
            table = self.query_one("#out-table", DataTable)
        except Exception:
            return
        table.clear(columns=True)
        table.add_column("#", width=4)
        table.add_column("날짜", width=10)
        table.add_column("금액", width=12)
        table.add_column("출처", width=18)
        table.add_column("가맹점/적요", width=30)
        table.add_column("추정 상대계정", width=16)
        for i, row in enumerate(self._rows, start=1):
            _, r_id = parse_counter_account(row.get("r", ""))
            counter = _account_title(self._session, r_id) or (row.get("right") or "")
            merchant = (row.get("r3") or row.get("detail") or "").strip()
            date = str(row.get("entry_date", ""))[:8]
            table.add_row(
                str(i), date, _money_text(row.get("money")),
                str(row.get("right") or "")[:18], merchant[:30], str(counter)[:16],
                key=str(row.get("out_id")),
            )

    def _render_all(self) -> None:
        self._render_summary()
        self._render_table()

    def _current(self) -> dict[str, Any] | None:
        if not self._rows:
            return None
        try:
            idx = max(0, self.query_one("#out-table", DataTable).cursor_row or 0)
        except Exception:  # pragma: no cover
            idx = 0
        if 0 <= idx < len(self._rows):
            return self._rows[idx]
        return None

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        self.last_status = msg
        try:
            st = self.query_one("#out-status", Static)
            st.update(msg)
            st.set_class(error, "error")
        except Exception:  # pragma: no cover
            pass

    # ---- navigation -----------------------------------------------------

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#out-table", DataTable).action_cursor_up()
        except Exception:  # pragma: no cover
            pass

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#out-table", DataTable).action_cursor_down()
        except Exception:  # pragma: no cover
            pass

    def action_close(self) -> None:
        self.dismiss(self._dirty)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "out-btn-close":
            self.action_close()
        elif bid == "out-btn-confirm":
            self.action_confirm()
        elif bid == "out-btn-delete":
            self.action_delete()
        elif bid == "out-btn-empty":
            self.action_empty()
        elif bid == "out-btn-refresh":
            self.action_refresh()

    # ---- load / refresh -------------------------------------------------

    def action_refresh(self) -> None:
        self._set_status("🌐 새로고침 중…")
        self._load_worker()

    @work(exclusive=True, group="outside_load", name="outside_load")
    async def _load_worker(self) -> None:
        try:
            rows = await self._outside.list_all(self._section_id)
        except Exception as e:  # noqa: BLE001
            log.exception("outside load failed")
            self._set_status(f"불러오기 실패: {e}", error=True)
            return
        self._rows = rows
        self._render_all()
        try:
            self.query_one("#out-table", DataTable).focus()
        except Exception:  # pragma: no cover
            pass
        if rows:
            self._set_status(f"✅ {len(rows)} 건 불러옴 — 확정(c)/삭제(d) 하세요.")
        else:
            self._set_status("임시저장소가 비어 있습니다.")

    # ---- confirm (입력) -------------------------------------------------

    def action_confirm(self) -> None:
        if self._busy:
            return
        row = self._current()
        if row is None:
            self._set_status("선택된 항목이 없습니다.")
            return
        from whooing_tui.screens.account_picker import AccountPickerScreen

        merchant = (row.get("r3") or row.get("detail") or "").strip()
        money = _money_text(row.get("money"))

        def _on_pick(result: tuple[str, str, str] | None) -> None:
            if result is None:
                self._set_status("입력 취소.")
                return
            account_id, _title, type_key = result
            self._confirm_worker(row, account_id, type_key)

        self.app.push_screen(
            AccountPickerScreen(
                self._session,
                side="left",
                purpose=f"'{merchant or '항목'}' {money} — 차변(지출) 계정 선택",
                default_expanded_type="expenses",
            ),
            _on_pick,
        )

    @work(exclusive=False, group="outside_mutate", name="outside_confirm")
    async def _confirm_worker(
        self, row: dict[str, Any], l_account_id: str, l_account: str,
    ) -> None:
        self._busy = True
        out_id = str(row.get("out_id"))
        entry = build_entry(
            row, l_account=l_account, l_account_id=l_account_id,
        )
        try:
            await self._outside.confirm(self._section_id, [entry], [out_id])
        except Exception as e:  # noqa: BLE001
            log.exception("outside confirm failed")
            self._set_status(f"입력 실패: {e}", error=True)
            self._busy = False
            return
        self._rows = [r for r in self._rows if str(r.get("out_id")) != out_id]
        self._dirty = True
        self._render_all()
        self._set_status(
            f"✅ '{entry['item']}' {_money_text(entry['money'])} 입력 완료 "
            f"(차변 {_account_title(self._session, l_account_id)})."
        )
        self._busy = False

    # ---- delete ---------------------------------------------------------

    def action_delete(self) -> None:
        if self._busy:
            return
        row = self._current()
        if row is None:
            self._set_status("선택된 항목이 없습니다.")
            return
        from whooing_tui.widgets.confirm import ConfirmModal

        merchant = (row.get("r3") or row.get("detail") or "").strip()

        def _on_confirm(ok: bool | None) -> None:
            if ok:
                self._delete_worker(row)

        self.app.push_screen(
            ConfirmModal(
                f"이 항목을 임시저장소에서 삭제할까요? (장부 입력 없이)\n"
                f"  {row.get('entry_date')} · {_money_text(row.get('money'))} · {merchant}",
                title="외부입력 삭제",
            ),
            _on_confirm,
        )

    @work(exclusive=False, group="outside_mutate", name="outside_delete")
    async def _delete_worker(self, row: dict[str, Any]) -> None:
        self._busy = True
        out_id = str(row.get("out_id"))
        try:
            await self._outside.delete(self._section_id, [out_id])
        except Exception as e:  # noqa: BLE001
            log.exception("outside delete failed")
            self._set_status(f"삭제 실패: {e}", error=True)
            self._busy = False
            return
        self._rows = [r for r in self._rows if str(r.get("out_id")) != out_id]
        self._dirty = True
        self._render_all()
        self._set_status("🗑️ 삭제 완료.")
        self._busy = False

    # ---- empty all ------------------------------------------------------

    def action_empty(self) -> None:
        if self._busy:
            return
        if not self._rows:
            self._set_status("비울 항목이 없습니다.")
            return
        from whooing_tui.widgets.confirm import ConfirmModal

        def _on_confirm(ok: bool | None) -> None:
            if ok:
                self._empty_worker()

        self.app.push_screen(
            ConfirmModal(
                f"임시저장소 전체({len(self._rows)} 건)를 비울까요?\n"
                f"되돌릴 수 없습니다.",
                title="임시저장소 전체 비우기",
            ),
            _on_confirm,
        )

    @work(exclusive=False, group="outside_mutate", name="outside_empty")
    async def _empty_worker(self) -> None:
        self._busy = True
        try:
            await self._outside.empty(self._section_id)
        except Exception as e:  # noqa: BLE001
            log.exception("outside empty failed")
            self._set_status(f"비우기 실패: {e}", error=True)
            self._busy = False
            return
        self._rows = []
        self._dirty = True
        self._render_all()
        self._set_status("🗑️ 임시저장소를 모두 비웠습니다.")
        self._busy = False
