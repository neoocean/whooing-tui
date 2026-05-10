"""ConfirmModal — yes/no confirmation modal.

CL #51156+ (review C6). 종전 3개 분리 정의 (`edit_entry.ConfirmModal`,
`monthly_entries._ConfirmModal`, `tag_management._ConfirmModal`) 통합.

dismiss(bool):
  - True  — Yes 버튼 / 'y' 키.
  - False — No 버튼 / 'n' 키 / Esc.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen[bool]):
    """기본 yes/no confirmation. 위험한 작업 (삭제 등) 에 사용."""

    BINDINGS = [
        Binding("escape", "no", "No"),
        Binding("y", "yes", "Yes"),
        Binding("n", "no", "No"),
    ]

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm_box {
        background: $panel;
        border: thick $warning;
        padding: 1;
        width: 95%;
        max-width: 60;
        min-width: 30;
        height: auto;
    }
    """

    def __init__(
        self, message: str,
        *,
        title: str = "확인",
        yes_label: str = "Yes (y)",
        no_label: str = "No (n/Esc)",
    ) -> None:
        super().__init__()
        self._message = message
        self._title = title
        self._yes_label = yes_label
        self._no_label = no_label

    def compose(self) -> ComposeResult:
        with Container(id="confirm_box"):
            if self._title:
                yield Static(f"[bold]{self._title}[/bold]")
            yield Static(self._message)
            with Horizontal():
                yield Button(self._yes_label, id="confirm_yes", variant="error")
                yield Button(self._no_label, id="confirm_no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm_yes")

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)
