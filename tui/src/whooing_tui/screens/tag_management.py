"""TagManagementScreen — 해시태그 일괄 관리 (rename / merge / delete).

CL #51135+ (H5). 누적 사용 시 가장 큰 마찰 — 오타 수정 한 건마다 entry 를
하나씩 edit dialog 로 열어야 했음. 본 화면이 일괄 도구.

키:
  ↑/↓        태그 선택 (DataTable cursor).
  enter / e  rename 모달 (현재 tag → 새 이름).
  m          merge 모달 (현재 tag → 다른 기존 tag 로 흡수).
  d          delete (확인 모달).
  r          새로고침.
  escape / q 뒤로.

각 mutation 직후 P4 자동 submit (db). description 은 LLM 미관여 mechanical.
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, Static,
)

from whooing_core import db as core_db

from whooing_tui import data as tui_data
from whooing_tui.ime import bind_ko
from whooing_tui.widgets import (
    MenuBar, MenuBarMixin, MenuItem, MenuSpec, menubar_bindings,
)

log = logging.getLogger(__name__)


# CL #51156+ (review C5/C6): 보조 modal 들이 widgets 의 통합으로 이동.
# `_TagInputModal` → `InputModal`, `_ConfirmModal` → `ConfirmModal`.
# 단순 wrapper 로 단일 태그 normalize ('#' strip) 만 추가.

from whooing_tui.widgets import ConfirmModal, InputModal


def _strip_tag_input(s: str | None) -> str | None:
    """태그 입력 normalize — `#` strip + 빈 None."""
    if s is None:
        return None
    v = s.strip().lstrip("#").strip()
    return v or None


# ---- Main screen --------------------------------------------------------


class TagManagementScreen(MenuBarMixin, ModalScreen[None]):
    """해시태그 일괄 관리 — list + rename / merge / delete + P4 submit.

    CL #52899+: 사용자 요청 — Screen → ModalScreen 으로 popup 화.
    """

    BINDINGS = [
        *menubar_bindings(),
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        Binding("enter", "rename", "Rename", show=True, priority=True),
        *bind_ko("e", "rename", "", show=False, priority=True),
        *bind_ko("m", "merge", "Merge", show=True, priority=True),
        *bind_ko("d", "delete", "Delete", show=True, priority=True),
        # CL #51151+ (H11): 색 변경.
        *bind_ko("c", "color", "Color", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    TagManagementScreen { align: center middle; }
    #tagm-frame {
        width: 95%;
        max-width: 120;
        min-width: 50;
        height: 90%;
        max-height: 40;
        min-height: 16;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
        layout: vertical;
    }
    #tagm-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #tagm_status {
        height: auto;
        padding: 1 0;
        background: transparent;
    }
    #tagm_status.error { color: $error; }
    #tagm_status.warn  { color: $warning; }
    #tagm_table { height: 1fr; }
    #tagm-foot {
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
                name="편집",
                items=(
                    MenuItem("이름 변경 (Enter)", "rename"),
                    MenuItem("병합 (m)", "merge"),
                    MenuItem("삭제 (d)", "delete"),
                    MenuItem("색 변경 (c)", "color"),
                ),
            ),
        )

    def _menubar_widget_id(self) -> str:
        return "tagm-menubar"

    def __init__(self, section_id: str | None = None) -> None:
        super().__init__()
        # section_id 명시 시 그 섹션만 / None 이면 전체 — 호출자 결정.
        self.section_id = section_id
        self.last_status: str = ""
        self._tags: list[tuple[str, int]] = []  # (tag, count) 사용 빈도 내림차순.

    def compose(self) -> ComposeResult:
        # CL #52899+: ModalScreen popup — Header/Footer 위젯 제거.
        with Vertical(id="tagm-frame"):
            yield Static("[bold]해시태그 관리[/bold]", id="tagm-title")
            yield MenuBar(self._build_menus(), id="tagm-menubar")
            yield Static("", id="tagm_status")
            yield DataTable(id="tagm_table", zebra_stripes=True, cursor_type="row")
            yield Static(
                "Enter 이름변경 · m 병합 · d 삭제 · c 색 · r 재로드 · Esc/q 닫기",
                id="tagm-foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#tagm_table", DataTable)
        table.add_columns("tag", "count")
        self.action_refresh()

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        # CL #52899+: ModalScreen 표준 종료 path.
        self.dismiss(None)

    def action_refresh(self) -> None:
        try:
            with tui_data.open_ro() as conn:
                counts = core_db.list_hashtags(conn, section_id=self.section_id)
                # CL #51151+ (H11): 태그 색 batch fetch.
                colors = core_db.get_tag_colors(
                    conn, section_id=self.section_id,
                )
        except Exception as ex:
            self._set_status(f"태그 조회 실패: {ex}", error=True)
            return
        self._tags = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        table = self.query_one("#tagm_table", DataTable)
        table.clear()
        for tag, n in self._tags:
            color = colors.get(tag)
            if color:
                # Rich markup 으로 색 적용.
                tag_label = f"[{color}]#{tag}[/{color}]"
            else:
                tag_label = f"#{tag}"
            table.add_row(tag_label, str(n), key=tag)
        scope = (
            f"섹션 {self.section_id}" if self.section_id else "전체 섹션"
        )
        self._set_status(
            f"{scope} — {len(self._tags)} 개 태그. "
            f"Enter=이름 변경 / m=병합 / d=삭제 / r=새로고침"
        )

    def _selected_tag(self) -> str | None:
        table = self.query_one("#tagm_table", DataTable)
        if not table.row_count:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            return str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            return None

    def action_rename(self) -> None:
        self._rename_worker()

    @work(exclusive=True, group="tagm", name="rename")
    async def _rename_worker(self) -> None:
        tag = self._selected_tag()
        if not tag:
            self._set_status("선택된 태그가 없습니다.", error=True)
            return
        raw = await self.app.push_screen_wait(InputModal(
            title="태그 이름 변경",
            prompt=f"#{tag} 를 어떤 이름으로 바꿀까요?",
            placeholder="새 태그 (# 자동)",
            initial=tag,
        ))
        new = _strip_tag_input(raw)
        if not new or new == tag:
            self._set_status("이름 변경 취소.")
            return
        await self._apply_rename(tag, new)

    async def _apply_rename(self, old: str, new: str) -> None:
        try:
            with tui_data.open_rw() as conn:
                result = core_db.rename_tag(
                    conn, old, new, section_id=self.section_id,
                )
        except Exception as ex:
            self._set_status(f"이름 변경 실패: {ex}", error=True)
            return
        # P4 자동 submit.
        from whooing_tui import p4_sync
        scope = f" section={self.section_id}" if self.section_id else ""
        p4_sync.submit_db_to_p4(
            tui_data.db_path(),
            f"[whooing-tui] hashtag rename {old} → {new}{scope}: "
            f"renamed={result['renamed']} merged={result['merged_into_existing']}",
        )
        self._set_status(
            f"#{old} → #{new} 적용: "
            f"{result['renamed']}건 변경 / {result['merged_into_existing']}건 병합."
        )
        self.action_refresh()

    def action_merge(self) -> None:
        self._merge_worker()

    @work(exclusive=True, group="tagm", name="merge")
    async def _merge_worker(self) -> None:
        tag = self._selected_tag()
        if not tag:
            self._set_status("선택된 태그가 없습니다.", error=True)
            return
        raw = await self.app.push_screen_wait(InputModal(
            title="태그 병합",
            prompt=f"#{tag} 를 어떤 기존 태그로 흡수할까요?",
            placeholder="기존 태그 이름",
            initial="",
        ))
        dest = _strip_tag_input(raw)
        if not dest or dest == tag:
            self._set_status("병합 취소.")
            return
        await self._apply_rename(tag, dest)

    def action_delete(self) -> None:
        self._delete_worker()

    @work(exclusive=True, group="tagm", name="delete")
    async def _delete_worker(self) -> None:
        tag = self._selected_tag()
        if not tag:
            self._set_status("선택된 태그가 없습니다.", error=True)
            return
        # count 회수.
        count = next((n for t, n in self._tags if t == tag), 0)
        scope = f" (section {self.section_id})" if self.section_id else ""
        ok = await self.app.push_screen_wait(ConfirmModal(
            f"#{tag} 태그를 모든 거래에서 영구 제거할까요?{scope}\n\n"
            f"  영향 받는 거래: {count}건\n\n"
            f"되돌릴 수 없습니다. (annotation/메모는 유지)",
            title="태그 삭제 확인",
        ))
        if not ok:
            self._set_status("삭제 취소.")
            return
        try:
            with tui_data.open_rw() as conn:
                deleted = core_db.delete_tag(
                    conn, tag, section_id=self.section_id,
                )
        except Exception as ex:
            self._set_status(f"삭제 실패: {ex}", error=True)
            return
        from whooing_tui import p4_sync
        p4_sync.submit_db_to_p4(
            tui_data.db_path(),
            f"[whooing-tui] hashtag delete {tag}{scope}: {deleted} rows",
        )
        self._set_status(f"#{tag} 제거 — {deleted}건 영향.")
        self.action_refresh()

    def action_color(self) -> None:
        self._color_worker()

    @work(exclusive=True, group="tagm", name="color")
    async def _color_worker(self) -> None:
        """현재 선택된 tag 의 색 설정 — `_TagInputModal` 재사용 (입력 = 색명).
        빈 값 → 색 제거 (default fallback).
        """
        from whooing_core import db as core_db
        tag = self._selected_tag()
        if not tag:
            self._set_status("선택된 태그가 없습니다.", error=True)
            return
        # 현재 색 prefill.
        current = ""
        try:
            with tui_data.open_ro() as conn:
                colors = core_db.get_tag_colors(conn, section_id=self.section_id)
            current = colors.get(tag, "")
        except Exception:  # pragma: no cover
            pass
        new = await self.app.push_screen_wait(InputModal(
            title=f"#{tag} 색 설정",
            prompt="색명 (Rich/Textual 표준 — 'red', 'cyan', 'magenta', '#ff8800' 등). 빈 값 = 색 제거.",
            placeholder="cyan / red / #ff8800",
            initial=current,
            accept_empty=True,   # 빈 입력 = 색 제거 신호로 사용.
        ))
        # InputModal 의 dismiss: None = Esc 취소, "" = 빈 입력 = 색 제거.
        if new is None:
            self._set_status("색 변경 취소.")
            return
        try:
            with tui_data.open_rw() as conn:
                core_db.set_tag_color(
                    conn, tag, new or None, section_id=self.section_id,
                )
        except Exception as ex:
            self._set_status(f"색 설정 실패: {ex}", error=True)
            return
        from whooing_tui import p4_sync
        scope = f" section={self.section_id}" if self.section_id else ""
        action = "set" if new else "clear"
        p4_sync.submit_db_to_p4(
            tui_data.db_path(),
            f"[whooing-tui] tag {tag} color {action} {new or ''}{scope}",
        )
        self._set_status(
            f"#{tag} 색 = {new or '(default)'}"
        )
        self.action_refresh()

    # ---- helpers ---------------------------------------------------------

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#tagm_status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
