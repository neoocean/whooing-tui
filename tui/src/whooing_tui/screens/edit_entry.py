"""EntryEditDialog — 거래 추가/수정 모달.

ModalScreen[EntryDraft | None] 으로 열린다. 사용자가 저장하면 EntryDraft
객체를 dismiss 결과로 반환하고, 취소 / esc 면 None.

호출자(EntriesScreen)는 dismiss 결과를 받아 WhooingClient.create_entry /
update_entry 를 부른다 — dialog 자체는 client 를 모른다 (테스트와
재사용을 위해).

UI 는 단순한 input 필드 6개:
  date     YYYYMMDD (편집 시 prefill, 새 입력 시 오늘)
  money    숫자 (천단위 콤마 입력 허용)
  left     account_id (예: x20). SessionState.id_of() 로 한국어 입력 보조
           — 이름을 입력해도 즉시 id 로 변환.
  right    같은 규칙
  item     적요 (선택)
  memo     메모 (선택)

자주입력·매월입력 매칭은 별도 CL (Phase 2d) — 본 dialog 는 manual 입력
fallback 만.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from whooing_tui.dates import parse_yyyymmdd, today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.state import SessionState


@dataclass
class EntryDraft:
    """사용자가 dialog 에서 확정한 거래 입력값.

    EntriesScreen 이 이 객체를 받아 client.create_entry / update_entry 의
    인자로 풀어 넣는다. l_account / r_account 는 SessionState 의 type 인덱스
    로부터 보강된다.
    """
    entry_date: str
    money: int
    l_account_id: str
    r_account_id: str
    item: str = ""
    memo: str = ""
    # 수정 모드면 entry_id, 새 입력이면 None.
    entry_id: str | None = None


def _strip_comma_int(s: str) -> int:
    """천단위 콤마 입력을 정수로. 빈 문자열은 0 이 아니라 ValueError."""
    cleaned = s.replace(",", "").strip()
    if not cleaned:
        raise ValueError("금액이 비어있습니다.")
    n = int(cleaned)  # int() 는 음수도 허용 — 호출자가 양수 검증
    return n


def _resolve_account(session: SessionState, raw: str) -> tuple[str, str] | None:
    """입력 (account_id 또는 표시명) → (account_id, type) 튜플.

    1) raw 가 'x' 로 시작하면 account_id 로 가정 → flat 에서 type 조회
    2) 아니면 title 로 매칭 (대소문자 무시)
    찾지 못하면 None.
    """
    if not raw:
        return None
    cand = raw.strip()
    if not cand:
        return None
    # account_id 직접 입력
    by_id = {a["account_id"]: a for a in session.accounts_flat}
    if cand in by_id:
        a = by_id[cand]
        return cand, a.get("type") or ""
    # title 매칭
    aid = session.id_of(cand)
    if aid is not None and aid in by_id:
        return aid, by_id[aid].get("type") or ""
    return None


class EntryEditDialog(ModalScreen[EntryDraft | None]):
    """거래 추가/수정 모달. dismiss(EntryDraft | None)."""

    DEFAULT_CSS = """
    EntryEditDialog {
        align: center middle;
    }
    #dialog-frame {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #dialog-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #form-grid {
        grid-size: 2 6;
        grid-columns: 9 1fr;
        grid-rows: 3 3 3 3 3 3;
        height: auto;
        padding: 1 0;
    }
    #form-grid Label {
        padding: 1 1 0 0;
        content-align: right middle;
    }
    #button-row {
        height: 3;
        align: center middle;
        padding-top: 1;
    }
    #button-row Button {
        margin: 0 1;
        min-width: 12;
    }
    #form-error {
        height: auto;
        color: $error;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True, priority=True),
    ]

    def __init__(
        self,
        session: SessionState,
        *,
        existing: dict[str, Any] | None = None,
    ) -> None:
        """`existing` 이 주어지면 수정 모드 (값 prefill + entry_id 보존)."""
        super().__init__()
        self._session = session
        self._existing = existing or {}
        self._is_edit = bool(self._existing.get("entry_id"))

    def compose(self) -> ComposeResult:
        title = "거래 수정" if self._is_edit else "거래 추가"
        with Vertical(id="dialog-frame"):
            yield Static(f"[bold]{title}[/bold]", id="dialog-title")
            with Grid(id="form-grid"):
                yield Label("date")
                yield Input(
                    value=self._existing.get("entry_date") or today_yyyymmdd(),
                    placeholder="YYYYMMDD", id="f-date", max_length=8,
                )
                yield Label("money")
                yield Input(
                    value=str(self._existing.get("money") or ""),
                    placeholder="숫자 (천단위 콤마 OK)", id="f-money",
                )
                yield Label("left")
                yield Input(
                    value=self._existing.get("l_account_id") or "",
                    placeholder="account_id (예: x20) 또는 항목명",
                    id="f-left",
                )
                yield Label("right")
                yield Input(
                    value=self._existing.get("r_account_id") or "",
                    placeholder="account_id (예: x11) 또는 항목명",
                    id="f-right",
                )
                yield Label("item")
                yield Input(
                    value=self._existing.get("item") or "",
                    placeholder="적요 (예: 스타벅스)", id="f-item",
                )
                yield Label("memo")
                yield Input(
                    value=self._existing.get("memo") or "",
                    placeholder="(선택)", id="f-memo",
                )
            yield Static("", id="form-error")
            with Horizontal(id="button-row"):
                yield Button("Save (Ctrl+S)", id="btn-save", variant="primary")
                yield Button("Cancel (Esc)", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#f-date", Input).focus()

    # ---- actions ------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        draft = self._build_draft()
        if isinstance(draft, EntryDraft):
            self.dismiss(draft)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.action_cancel()
        elif event.button.id == "btn-save":
            self.action_save()

    # ---- form → draft -------------------------------------------------

    def _build_draft(self) -> EntryDraft | str | None:
        """폼 값을 EntryDraft 로. 검증 실패 시 form-error 에 메시지를 쓰고
        None 을 반환 (dialog 는 닫지 않는다)."""
        v = lambda wid: self.query_one(f"#{wid}", Input).value  # noqa: E731

        try:
            date = parse_yyyymmdd(v("f-date"))
        except ValueError as e:
            self._show_error(f"date: {e}")
            return None
        try:
            money = _strip_comma_int(v("f-money"))
        except ValueError as e:
            self._show_error(f"money: {e}")
            return None
        if money <= 0:
            self._show_error("money 는 양수여야 합니다 (음양은 차변/대변으로 표현).")
            return None

        left = _resolve_account(self._session, v("f-left"))
        if left is None:
            self._show_error(f"left: account_id / 항목명 매칭 실패 — {v('f-left')!r}")
            return None
        right = _resolve_account(self._session, v("f-right"))
        if right is None:
            self._show_error(f"right: account_id / 항목명 매칭 실패 — {v('f-right')!r}")
            return None
        if left[0] == right[0]:
            self._show_error("left 와 right 는 서로 다른 항목이어야 합니다.")
            return None

        return EntryDraft(
            entry_date=date,
            money=money,
            l_account_id=left[0],
            r_account_id=right[0],
            item=v("f-item").strip(),
            memo=v("f-memo").strip(),
            entry_id=self._existing.get("entry_id"),
        )

    def _show_error(self, msg: str) -> None:
        self.query_one("#form-error", Static).update(msg)


class ConfirmModal(ModalScreen[bool]):
    """짧은 yes/no 확인 모달. dismiss(True/False).

    삭제처럼 되돌릴 수 없는 작업 직전에 띄운다.
    """

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm-frame {
        width: 56;
        height: auto;
        padding: 1 2;
        border: thick $error;
        background: $surface;
    }
    #confirm-title {
        height: 1;
        content-align: center middle;
        color: $error;
    }
    #confirm-message {
        padding: 1 0;
        height: auto;
    }
    #confirm-buttons {
        height: 3;
        align: center middle;
    }
    #confirm-buttons Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    BINDINGS = [
        Binding("escape", "no", "No", show=True),
        *bind_ko("y", "yes", "Yes", show=True, priority=True),
        *bind_ko("n", "no", "No", show=True, priority=True),
    ]

    def __init__(self, message: str, *, title: str = "확인") -> None:
        super().__init__()
        self._message = message
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-frame"):
            yield Static(f"[bold]{self._title}[/bold]", id="confirm-title")
            yield Static(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes (y)", id="btn-yes", variant="error")
                yield Button("No (n)", id="btn-no")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-yes":
            self.action_yes()
        elif event.button.id == "btn-no":
            self.action_no()
