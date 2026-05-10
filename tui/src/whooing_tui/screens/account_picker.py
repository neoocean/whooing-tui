"""AccountPickerScreen — `EntryEditDialog` 의 left/right 선택 모달.

거래 입력/수정 폼에서 left/right 버튼에 Enter 를 누르면 본 모달이 push 된다.
사용자가 OptionList 에서 계정과목을 선택하면 `(account_id, title, type_key)`
튜플을 dismiss 결과로 반환. 취소(Esc/q) 면 None.

OptionList 의 type-to-search 가 사용자가 식비 / 현금 같은 친숙한 이름을
빠르게 찾도록 도와준다.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from whooing_tui.ime import bind_ko
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


# 후잉 표준 type 표시 순서 — assets → liabilities → capital → income →
# expenses → group. 같은 type 안에서는 list 받은 순서.
_TYPE_ORDER = ("assets", "liabilities", "capital", "income", "expenses", "group")
_TYPE_LABEL = {
    "assets": "자산",
    "liabilities": "부채",
    "capital": "자본",
    "income": "수입",
    "expenses": "지출",
    "group": "그룹",
}


class AccountPickerScreen(ModalScreen[tuple[str, str, str] | None]):
    """계정과목 선택 모달. dismiss((account_id, title, type_key) | None)."""

    DEFAULT_CSS = """
    AccountPickerScreen {
        align: center middle;
    }
    #picker-frame {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #picker-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #picker-hint {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    OptionList {
        height: auto;
        max-height: 22;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        *bind_ko("q", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        session: SessionState,
        *,
        side: str,
        current_id: str | None = None,
    ) -> None:
        """`side` = "left" / "right" — 모달 타이틀 라벨용."""
        super().__init__()
        self._session = session
        self._side = side
        self._current = current_id

    def compose(self) -> ComposeResult:
        side_label = "차변 (left)" if self._side == "left" else "대변 (right)"
        with Vertical(id="picker-frame"):
            yield Static(
                f"[bold]계정과목 선택 — {side_label}[/bold]", id="picker-title",
            )
            yield Static("타이핑으로 검색 / Enter 선택 / Esc 취소", id="picker-hint")
            yield OptionList(id="acc-list")

    def on_mount(self) -> None:
        opt = self.query_one("#acc-list", OptionList)
        accounts = self._sorted_accounts()
        highlight_idx = 0
        for i, a in enumerate(accounts):
            aid = str(a.get("account_id") or "")
            title = a.get("title") or "(no title)"
            type_key = a.get("type") or ""
            type_label = _TYPE_LABEL.get(type_key, type_key)
            label = f"{title}  [dim]{aid} · {type_label}[/dim]"
            opt.add_option(Option(label, id=aid))
            if aid == self._current:
                highlight_idx = i
        if accounts:
            opt.highlighted = highlight_idx
        opt.focus()

    def _sorted_accounts(self) -> list[dict[str, Any]]:
        """type 표준 순서로 정렬한 flat 계정 list."""
        flat = list(self._session.accounts_flat)
        order = {t: i for i, t in enumerate(_TYPE_ORDER)}
        return sorted(flat, key=lambda a: (order.get(a.get("type", ""), 99),))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        sid = event.option.id
        if not sid:
            return
        for a in self._session.accounts_flat:
            if a.get("account_id") == sid:
                self.dismiss((sid, a.get("title") or "", a.get("type") or ""))
                return
