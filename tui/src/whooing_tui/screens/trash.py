"""TrashScreen — 소프트삭제(휴지통) 거래 목록 + 복원 / 영구삭제.

시나리오 11. dismiss 값:
  - `("restore", logical_id)` — 복원 요청 (caller 가 후잉 재생성).
  - `("purge", logical_id)` — 영구삭제 요청 (caller 가 확인 후 purge).
  - `None` — 닫기.

후잉 mutation 은 *하지 않는다* — EntriesScreen 워커가 결과를 받아 수행
(MenuPopup / context-menu 와 동일 패턴).
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


def _fmt_money(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v if v is not None else "")


class TrashScreen(ModalScreen["tuple[str, str] | None"]):
    BINDINGS = [
        Binding("escape", "cancel", "닫기"),
        Binding("q", "cancel", "닫기"),
        Binding("enter", "restore", "복원"),
        Binding("r", "restore", "복원"),
        Binding("x", "purge", "영구삭제"),
    ]

    DEFAULT_CSS = """
    TrashScreen { align: center middle; }
    #trash-frame {
        width: 90%; max-width: 120; min-width: 40;
        height: 80%; max-height: 40; min-height: 10;
        background: $panel; border: solid $primary; padding: 0 1;
    }
    #trash-title { color: $accent; text-style: bold; height: 1; }
    #trash-hint { color: $text-muted; height: 1; }
    #trash-list { height: 1fr; }
    """

    def __init__(self, deleted: list[dict[str, Any]]) -> None:
        super().__init__()
        self._deleted = deleted

    def compose(self) -> ComposeResult:
        with Container(id="trash-frame"):
            yield Static(
                f"휴지통 — 삭제된 거래 {len(self._deleted)}건", id="trash-title",
            )
            yield Static(
                "Enter/r 복원 · x 영구삭제 · Esc 닫기", id="trash-hint",
            )
            options: list[Option] = []
            for d in self._deleted:
                label = (
                    f"{str(d.get('entry_date') or '')[:8]:<10} "
                    f"{_fmt_money(d.get('money')):>12}  "
                    f"{(d.get('item') or '')[:30]}"
                    f"   (삭제 {str(d.get('deleted_at') or '')[:16]})"
                )
                options.append(Option(label, id=str(d.get("logical_id"))))
            yield OptionList(*options, id="trash-list")

    def on_mount(self) -> None:
        try:
            self.query_one("#trash-list", OptionList).focus()
        except Exception:  # pragma: no cover
            pass

    def _selected_logical(self) -> str | None:
        try:
            ol = self.query_one("#trash-list", OptionList)
            if ol.highlighted is None:
                return None
            return ol.get_option_at_index(ol.highlighted).id
        except Exception:  # pragma: no cover
            return None

    def action_restore(self) -> None:
        lid = self._selected_logical()
        if lid:
            self.dismiss(("restore", lid))

    def action_purge(self) -> None:
        lid = self._selected_logical()
        if lid:
            self.dismiss(("purge", lid))

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["TrashScreen"]
