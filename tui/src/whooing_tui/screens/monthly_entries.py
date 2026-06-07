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
from textual.screen import ModalScreen
from textual.widgets import (
    Button, DataTable, Input, Label, Static,
)

from whooing_tui.client import WhooingClient
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.screens.account_picker import AccountPickerScreen
from whooing_tui.screens.edit_entry import _AccountButton
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
    """

    def __init__(self, session: SessionState) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        with Container(id="me_box"):
            yield Label("[bold]매월 입력 거래 등록[/bold]")
            yield Label("target_day (1~31)")
            yield Input(placeholder="25", id="me-day")
            yield Label("money (KRW)")
            yield Input(placeholder="50000", id="me-money")
            # CL #56830+ (C13): raw account_id 텍스트 입력 → EntryEditDialog 와
            # 동일한 AccountPickerScreen 트리 선택. 버튼 Enter → picker push.
            yield Label("차변 (left) — Enter 로 선택")
            yield _AccountButton(button_id="me-left")
            yield Label("대변 (right) — Enter 로 선택")
            yield _AccountButton(button_id="me-right")
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
            left = self.query_one("#me-left", _AccountButton)
            right = self.query_one("#me-right", _AccountButton)
            item = self.query_one("#me-item", Input).value.strip()
            memo = self.query_one("#me-memo", Input).value.strip()
        except (ValueError, AttributeError) as e:
            self.notify(f"입력 오류: {e}", severity="error")
            return
        if not (1 <= day <= 31):
            self.notify("target_day 는 1~31 사이여야 합니다.", severity="error")
            return
        if not (left.account_id and right.account_id and money > 0):
            self.notify("차변·대변 계정 + 금액 모두 필수.", severity="error")
            return
        self.dismiss({
            "target_day": day, "money": money,
            "l_account": left.type_key, "l_account_id": left.account_id,
            "r_account": right.type_key, "r_account_id": right.account_id,
            "item": item, "memo": memo,
        })

    def _open_account_picker(self, button_id: str) -> None:
        """left/right 버튼 → AccountPickerScreen push, 결과로 버튼 갱신.

        EntryEditDialog._open_account_picker 와 동일 패턴. 본 modal 은
        그동안 push stack 아래에서 살아있다가 콜백으로 버튼만 갱신.
        """
        side = "left" if button_id == "me-left" else "right"
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
        if bid == "me-save":
            self.action_save()
        elif bid == "me-cancel":
            self.action_cancel()
        elif bid in ("me-left", "me-right"):
            self._open_account_picker(bid)


# CL #51156+ (review C6): `_ConfirmModal` → `widgets.ConfirmModal` 사용.

from whooing_tui.widgets import ConfirmModal as _ConfirmModal  # 호환 alias.


# ---- Main screen --------------------------------------------------------


class MonthlyEntriesScreen(MenuBarMixin, ModalScreen[None]):
    """매월 입력 거래 list + 추가/삭제.

    CL #52896+: 사용자 요청 — 전체 화면 Screen → ModalScreen 으로 변경.
    뒷 화면 (EntriesScreen) 이 살짝 보이는 popup 형태.
    """

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("n", "new_entry", "New", show=True, priority=True),
        *bind_ko("d", "delete_entry", "Delete", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    MonthlyEntriesScreen { align: center middle; }
    #m_frame {
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
    #m_title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #m_status {
        height: auto;
        padding: 1 0;
        background: transparent;
    }
    #m_status.error { color: $error; }
    #m_status.warn  { color: $warning; }
    #m_table { height: 1fr; }
    #m_foot {
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
        # CL #52896+: ModalScreen 으로 — 가운데 정렬 #m_frame 안에 menubar
        # / 상태 / 표 / footer hint. Header / Footer 는 modal 안에서 무의미.
        with Vertical(id="m_frame"):
            yield Static("[bold]매월 입력 거래[/bold]", id="m_title")
            yield MenuBar(self._build_menus(), id="monthly-menubar")
            yield Static("", id="m_status")
            yield DataTable(id="m_table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "n 새 매월거래 · d 삭제 · r 재로드 · Esc/q 닫기",
                id="m_foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#m_table", DataTable)
        table.add_columns("id", "day", "left", "right", "money", "item")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------
    #
    # CL #56830+ worker group 정책: refresh / new / delete 는 *서로 다른*
    # exclusive group 을 쓴다. 종전엔 셋 다 group="monthly" 라, on_mount 의
    # refresh 가 in-flight 인 동안 사용자가 n (new) 를 누르면 _new_worker 가
    # 시작되며 같은 group 의 refresh 를 cancel — 반대로 refresh 가 나중에
    # 시작되면 push_screen_wait 로 modal 을 await 중인 _new_worker 를 cancel
    # 해 사용자의 저장이 조용히 유실됐다 (CLAUDE.md 함정 #3). group 을 쪼개
    # refresh 가 사용자 다이얼로그를 죽이지 못하게 한다. 같은 group 내 중복
    # (rapid r / rapid n) 만 자동 cancel.

    def action_back(self) -> None:
        # CL #52896+: ModalScreen 의 표준 종료 path — dismiss.
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._refresh_worker()

    @work(exclusive=True, group="monthly-refresh", name="refresh")
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

    @work(exclusive=True, group="monthly-new", name="new")
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

    @work(exclusive=True, group="monthly-delete", name="delete")
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
