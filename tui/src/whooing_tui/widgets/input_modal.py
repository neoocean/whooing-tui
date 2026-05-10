"""InputModal — 단일/다중 라인 입력 modal 통합.

CL #51156+ (review C5). 종전엔 7개 modal 이 거의 동일 구조로 분산:
  - `_FilePathModal` (entries.py) / `_AddPathModal` (attachment_browser)
  - `_TagInputModal` (tag_management) / `_AmountModal` (budget_edit)
  - `_NoteEditModal` (attachment_browser, multi-line TextArea)
  - `_GoalEditModal` (goal_edit, 2 fields)
  - `_MonthlyEditModal` (monthly_entries, 8 fields)

본 모듈이 단일 라인 (`InputModal`) + 다중 라인 (`TextAreaModal`) 의 base.
복잡한 multi-field modal (Goal/Monthly) 은 본 모듈의 패턴 재사용 + 자체
compose 가 더 자연스럽지만, *공통 BINDINGS / DEFAULT_CSS / OK/Cancel 버튼
처리* 는 `_BaseInputModal` 로 공유.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea


class _BaseInputModal(ModalScreen):
    """OK/Cancel 버튼 + Esc/Ctrl+S 바인딩 공통.

    파생 클래스는:
      - `_compose_body()` 로 입력 위젯들 yield.
      - `_collect_value()` 로 OK 시 dismiss 값 결정.
      - `_focus_target()` 으로 on_mount focus 위젯 (default = 첫 Input).
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    _BaseInputModal {
        align: center middle;
    }
    #im_box {
        background: $panel;
        border: thick $primary;
        padding: 1 2;
        width: 95%;
        max-width: 70;
        min-width: 30;
        height: auto;
    }
    """

    def __init__(self, *, title: str = "") -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Container(id="im_box"):
            if self._title:
                yield Label(f"[bold]{self._title}[/bold]")
            yield from self._compose_body()
            with Horizontal():
                yield Button("Save (Ctrl+S)", id="im_ok", variant="primary")
                yield Button("Cancel (Esc)", id="im_cancel")

    def _compose_body(self) -> ComposeResult:  # pragma: no cover — override.
        return iter(())

    def _collect_value(self) -> Any:  # pragma: no cover — override.
        return None

    def _focus_target(self) -> str | None:
        """default — 첫 Input 또는 TextArea 의 id (override 가능)."""
        for widget_id in ("im_input", "im_text"):
            try:
                self.query_one(f"#{widget_id}")
                return widget_id
            except Exception:  # pragma: no cover
                continue
        return None

    def on_mount(self) -> None:
        target = self._focus_target()
        if target:
            try:
                self.query_one(f"#{target}").focus()
            except Exception:  # pragma: no cover
                pass

    def action_save(self) -> None:
        self.dismiss(self._collect_value())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "im_ok":
            self.action_save()
        else:
            self.action_cancel()


class InputModal(_BaseInputModal):
    """단일 라인 Input modal. dismiss(str | None).

    `accept_empty=False` (default) 면 빈 입력은 None dismiss.
    """

    def __init__(
        self,
        *,
        title: str = "",
        prompt: str = "",
        placeholder: str = "",
        initial: str = "",
        accept_empty: bool = False,
    ) -> None:
        super().__init__(title=title)
        self._prompt = prompt
        self._placeholder = placeholder
        self._initial = initial
        self._accept_empty = accept_empty

    def _compose_body(self) -> ComposeResult:
        if self._prompt:
            yield Label(self._prompt)
        yield Input(
            value=self._initial, placeholder=self._placeholder, id="im_input",
        )

    def _collect_value(self) -> str | None:
        try:
            v = self.query_one("#im_input", Input).value.strip()
        except Exception:  # pragma: no cover
            return None
        if not v and not self._accept_empty:
            return None
        return v

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()


class TextAreaModal(_BaseInputModal):
    """multi-line TextArea modal. dismiss(str | None).

    빈 문자열도 dismiss (caller 가 NULL 정규화 의도) — `None` 은 Esc 만.
    """

    def __init__(
        self,
        *,
        title: str = "",
        prompt: str = "",
        initial: str = "",
        height: int = 6,
    ) -> None:
        super().__init__(title=title)
        self._prompt = prompt
        self._initial = initial
        self._height = height

    def _compose_body(self) -> ComposeResult:
        if self._prompt:
            yield Label(self._prompt)
        ta = TextArea(self._initial, id="im_text")
        ta.styles.height = self._height
        yield ta

    def _collect_value(self) -> str:
        try:
            return self.query_one("#im_text", TextArea).text
        except Exception:  # pragma: no cover
            return ""

    def _focus_target(self) -> str:
        return "im_text"
