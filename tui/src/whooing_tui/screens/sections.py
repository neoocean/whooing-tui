"""SectionPickerScreen — 섹션 선택 모달.

EntriesScreen 에서 `s` 키로 push. 사용자가 섹션을 고르면 dismiss 결과로
`(section_id, title)` 튜플을 반환하고, 취소 / esc 면 `None`. 호출자
(EntriesScreen) 가 dismiss 결과를 받아 SessionState.set_section() 후
재로드한다.

UI:
- OptionList — 사용 가능한 섹션 + 현재 활성 섹션 인디케이터
- Enter 선택 → dismiss((sid, title))
- Esc / q → dismiss(None)
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from whooing_tui.client import WhooingClient
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError

log = logging.getLogger(__name__)


class SectionPickerScreen(ModalScreen[tuple[str, str | None] | None]):
    """섹션 선택 모달. dismiss((section_id, title) | None)."""

    DEFAULT_CSS = """
    SectionPickerScreen {
        align: center middle;
    }
    #picker-frame {
        /* CL #51120+: 좁은 터미널 대응. */
        width: 95%;
        max-width: 56;
        min-width: 30;
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
    #picker-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #picker-status.error {
        color: $error;
    }
    OptionList {
        height: auto;
        max-height: 16;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        *bind_ko("q", "cancel", "Cancel", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True),
    ]

    def __init__(
        self,
        client: WhooingClient,
        current_section_id: str | None = None,
    ) -> None:
        super().__init__()
        self._client = client
        self._current = current_section_id
        self._sections_by_id: dict[str, dict[str, Any]] = {}
        # 평문 status (테스트 친화).
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-frame"):
            yield Static("[bold]섹션 선택[/bold]", id="picker-title")
            yield OptionList(id="sections-list")
            yield Static("", id="picker-status")

    def on_mount(self) -> None:
        self.set_status("섹션 목록을 불러오는 중…")
        self.query_one("#sections-list", OptionList).focus()
        self.refresh_sections()

    # ---- actions ------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        invalidate = getattr(self._client, "invalidate_section", None)
        # SectionPicker 는 sections-list 만 사용 — 캐시는 sections 단계에선
        # 안 두므로 client wrapper 의 invalidate_section 은 의미 없지만,
        # 일관성을 위해 호출한다 (raw client 면 callable 아님).
        if self._current and callable(invalidate):
            invalidate(self._current)
        self.set_status("재로드 중…")
        self.refresh_sections()

    # ---- worker -------------------------------------------------------

    @work(exclusive=True, group="picker", name="refresh_sections")
    async def refresh_sections(self) -> None:
        try:
            sections = await self._client.list_sections()
        except ToolError as e:
            self.set_status(f"섹션 로드 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("section picker: list_sections failed")
            self.set_status(f"섹션 로드 실패 (INTERNAL): {e}", error=True)
            return

        self._sections_by_id = {
            str(s.get("section_id") or s.get("id")): s for s in sections
        }
        opt = self.query_one("#sections-list", OptionList)
        opt.clear_options()

        if not sections:
            opt.add_option(Option("(섹션 없음)", disabled=True, id="__empty__"))
            self.set_status(
                "후잉 계정에 섹션이 없습니다 — whooing.com 에서 먼저 생성하세요.",
                error=True,
            )
            return

        # 현재 활성 섹션은 라벨 앞에 ▶ 인디케이터.
        highlight_idx = 0
        for i, s in enumerate(sections):
            sid = str(s.get("section_id") or s.get("id"))
            title = s.get("title") or "(no title)"
            mark = "▶ " if sid == self._current else "  "
            opt.add_option(Option(f"{mark}{title}  [dim]{sid}[/dim]", id=sid))
            if sid == self._current:
                highlight_idx = i
        opt.highlighted = highlight_idx
        self.set_status(f"섹션 {len(sections)}개. Enter 선택 / Esc 취소.")

    # ---- option list 이벤트 -------------------------------------------

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        sid = event.option.id
        if not sid or sid == "__empty__":
            return
        title = self._sections_by_id.get(sid, {}).get("title")
        self.dismiss((sid, title))

    # ---- status -------------------------------------------------------

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.last_status = text
        bar = self.query_one("#picker-status", Static)
        bar.update(text)
        bar.remove_class("error")
        if error:
            bar.add_class("error")
