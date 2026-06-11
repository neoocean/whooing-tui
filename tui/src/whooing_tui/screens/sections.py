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
from whooing_tui.widgets.confirm import ConfirmModal
from whooing_tui.widgets.input_modal import InputModal

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
        # 0.84.0 (로드맵 P3-C): 섹션 CRUD + 순서 변경.
        *bind_ko("n", "new_section", "New", show=True, priority=True),
        *bind_ko("e", "rename_section", "Rename", show=True, priority=True),
        *bind_ko("d", "delete_section", "Delete", show=True, priority=True),
        Binding("left_square_bracket", "move_up", "↑순서", show=True,
                priority=True, key_display="["),
        Binding("right_square_bracket", "move_down", "↓순서", show=True,
                priority=True, key_display="]"),
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
        # 표시 순서대로의 섹션 dict list — 로컬 재정렬/재렌더의 source.
        self._sections: list[dict[str, Any]] = []
        # 순서 저장 디바운스 타이머 — 연속 이동 시 마지막 1회만 네트워크.
        self._sort_timer: Any = None
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

    # ---- CRUD actions (0.84.0) ----------------------------------------

    def action_new_section(self) -> None:
        self._new_section_flow()

    def action_rename_section(self) -> None:
        sid = self._highlighted_sid()
        if not sid:
            self.set_status("선택된 섹션이 없습니다.", error=True)
            return
        self._rename_section_flow(sid)

    def action_delete_section(self) -> None:
        sid = self._highlighted_sid()
        if not sid:
            self.set_status("선택된 섹션이 없습니다.", error=True)
            return
        self._delete_section_flow(sid)

    def action_move_up(self) -> None:
        self._reorder(-1)

    def action_move_down(self) -> None:
        self._reorder(+1)

    @work(exclusive=True, group="section-mutate", name="new_section")
    async def _new_section_flow(self) -> None:
        title = await self.app.push_screen_wait(InputModal(
            title="새 섹션", prompt="섹션 제목 (1~30자):",
            placeholder="예: 가계부 2026",
        ))
        if not title:
            self.set_status("섹션 생성 취소됨.")
            return
        try:
            await self._client.create_section(title=title)
        except ToolError as e:
            self.set_status(f"섹션 생성 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("create_section failed")
            self.set_status(f"섹션 생성 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(f"섹션 추가 완료 ({title}). 재로드 중…")
        self.refresh_sections()

    @work(exclusive=True, group="section-mutate", name="rename_section")
    async def _rename_section_flow(self, sid: str) -> None:
        cur = self._sections_by_id.get(sid, {}).get("title") or ""
        title = await self.app.push_screen_wait(InputModal(
            title="섹션 이름 변경", prompt="새 제목 (1~30자):", initial=cur,
        ))
        if not title or title == cur:
            self.set_status("이름 변경 취소됨.")
            return
        try:
            await self._client.update_section(section_id=sid, title=title)
        except ToolError as e:
            self.set_status(f"이름 변경 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("update_section failed")
            self.set_status(f"이름 변경 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(f"섹션 이름 변경 완료 ({title}). 재로드 중…")
        self.refresh_sections()

    @work(exclusive=True, group="section-mutate", name="delete_section")
    async def _delete_section_flow(self, sid: str) -> None:
        title = self._sections_by_id.get(sid, {}).get("title") or sid
        warn = "" if sid != self._current else (
            "\n\n[bold red]⚠ 현재 활성 섹션입니다.[/bold red]"
        )
        ok = await self.app.push_screen_wait(ConfirmModal(
            f"섹션 '{title}' ({sid}) 을(를) 삭제할까요?\n"
            f"딸린 거래·항목이 함께 삭제되며 되돌릴 수 없습니다.{warn}",
            title="섹션 삭제",
        ))
        if not ok:
            self.set_status("삭제 취소됨.")
            return
        try:
            await self._client.delete_section(section_id=sid)
        except ToolError as e:
            self.set_status(f"섹션 삭제 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("delete_section failed")
            self.set_status(f"섹션 삭제 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(f"섹션 삭제 완료 ({title}). 재로드 중…")
        self.refresh_sections()

    def _reorder(self, delta: int) -> None:
        """하이라이트 섹션을 delta 만큼 이동 + 로컬 재렌더 + 서버 영속화."""
        opt = self.query_one("#sections-list", OptionList)
        idx = opt.highlighted
        if idx is None or not self._sections:
            return
        j = idx + delta
        if j < 0 or j >= len(self._sections):
            return
        moved_sid = self._sid_of(self._sections[idx])
        self._sections[idx], self._sections[j] = (
            self._sections[j], self._sections[idx],
        )
        # 즉시 로컬 재렌더 (서버 왕복 없이 반응적).
        self._render_options(highlight_sid=moved_sid)
        # 저장은 디바운스 (감사 2026-06 §2-F): 연속 [/] 이동 시 매 keypress
        # round-trip 대신 멈춘 뒤 1회만 sort_sections.
        if self._sort_timer is not None:
            self._sort_timer.stop()
        ids = [self._sid_of(s) for s in self._sections]
        self._sort_timer = self.set_timer(0.4, lambda: self._persist_sort(ids))

    @work(exclusive=True, group="section-mutate", name="sort_sections")
    async def _persist_sort(self, ids: list[str]) -> None:
        try:
            await self._client.sort_sections(section_ids=ids)
        except ToolError as e:
            self.set_status(f"순서 저장 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("sort_sections failed")
            self.set_status(f"순서 저장 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status("섹션 순서 저장됨.")

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

        self._sections = list(sections)
        self._sections_by_id = {
            str(s.get("section_id") or s.get("id")): s for s in sections
        }
        self._render_options(highlight_sid=self._current)
        if sections:
            self.set_status(
                f"섹션 {len(sections)}개. Enter 선택 · n 새로 · e 이름 · "
                "d 삭제 · [ ] 순서."
            )

    def _sid_of(self, s: dict[str, Any]) -> str:
        return str(s.get("section_id") or s.get("id"))

    def _render_options(self, *, highlight_sid: str | None = None) -> None:
        """`self._sections` (현재 순서) 로 OptionList 재구성."""
        opt = self.query_one("#sections-list", OptionList)
        opt.clear_options()
        if not self._sections:
            opt.add_option(Option("(섹션 없음)", disabled=True, id="__empty__"))
            self.set_status(
                "후잉 계정에 섹션이 없습니다 — n 으로 새 섹션을 만들거나 "
                "whooing.com 에서 생성하세요.",
                error=True,
            )
            return
        highlight_idx = 0
        for i, s in enumerate(self._sections):
            sid = self._sid_of(s)
            title = s.get("title") or "(no title)"
            mark = "▶ " if sid == self._current else "  "
            opt.add_option(Option(f"{mark}{title}  [dim]{sid}[/dim]", id=sid))
            if sid == highlight_sid:
                highlight_idx = i
        opt.highlighted = highlight_idx

    def _highlighted_sid(self) -> str | None:
        opt = self.query_one("#sections-list", OptionList)
        idx = opt.highlighted
        if idx is None or idx < 0 or idx >= len(self._sections):
            return None
        return self._sid_of(self._sections[idx])

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
