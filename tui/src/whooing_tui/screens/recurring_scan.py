"""반복거래누락 검사 화면 — 범위 선택 · 진행 popup · 결과 overview.

`whooing_core.recurring.detect_recurring_omissions` 가 찾은 *누락 있는*
반복 시리즈를 한 화면에서 보여주고, 사용자가 각 시리즈를 처리(handled) 하거나
무시(dismissed) 하도록 한다. 중복 스캔(`dupe_scan_overview.py`) 과 대칭 흐름:

  1. EntriesScreen "입력" 메뉴 → "반복 거래 누락 검사…" →
     `_scan_recurring_worker()` 가 범위 선택 → fetch → detect → sqlite 저장.
  2. RecurringOmissionScreen 가 시리즈 목록 + 선택 시리즈의 누락 회차 상세.
  3. h — 현재 시리즈 '처리함', d — '무시' (둘 다 다음 스캔에서 안 보임).
  4. F5 — 후잉에서 새로고침. Esc — 닫기.

거래를 직접 생성/삭제하지 않으므로 client 는 필요 없다 — 상태 변경은 repo
(sqlite) 에 바로 기록.

dismiss 값:
  True  — 한 건이라도 처리/무시했음 (호출자 참고용).
  False — 변경 없이 닫음.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, OptionList, Static
from textual.widgets.option_list import Option

from whooing_core.recurring import CADENCE_LABELS_KO

from whooing_tui.ime import bind_ko
from whooing_tui.recurring_scan_repo import RecurringScanRepository, StoredSeries
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


from whooing_tui.text_utils import fmt_money as _fmt_money


def _account_title(session: SessionState, account_id: str) -> str:
    """account_id → 사람이 읽는 항목명. 못 찾으면 id 그대로."""
    if not account_id:
        return ""
    try:
        title = session.title_of(account_id)
        if title:
            return str(title)
    except Exception:
        pass
    return account_id


# refresh callback: 후잉에서 다시 fetch 후 새 StoredSeries list 반환.
RefreshCallback = Callable[[], Awaitable[list[StoredSeries]]]


class RecurringScanRangeModal(ModalScreen[int | None]):
    """반복거래누락 검사 범위 선택 popup.

    반복 주기 추정에는 충분한 과거가 필요하므로 기본 1년 (월간 12회 확보).
    `dismiss` 값 = 검사할 일수 (int). Esc → None.
    """

    OPTIONS: tuple[tuple[str, int], ...] = (
        ("6 개월", 180),
        ("1 년 (기본)", 365),
        ("2 년", 365 * 2),
        ("3 년", 365 * 3),
        ("5 년", 365 * 5),
    )
    DEFAULT_DAYS: int = 365

    DEFAULT_CSS = """
    RecurringScanRangeModal {
        align: center middle;
    }
    #rec-range-frame {
        width: auto;
        min-width: 44;
        max-width: 64;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #rec-range-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #rec-range-prompt {
        height: auto;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #rec-range-list {
        height: auto;
        max-height: 10;
        margin-top: 1;
    }
    #rec-range-hint {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소", show=True),
    ]

    def __init__(self, default_days: int | None = None) -> None:
        super().__init__()
        self._initial_days: int = default_days or self.DEFAULT_DAYS
        self.last_choice: int | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="rec-range-frame"):
            yield Static(
                "[bold]🔁 반복 거래 누락 검사 — 범위 선택[/bold]",
                id="rec-range-title",
            )
            yield Static(
                "정기 구독·월세·급여처럼 규칙적으로 반복되는 거래가\n"
                "빠진 회차를 찾습니다. 얼마나 과거까지 검사할까요?",
                id="rec-range-prompt",
            )
            yield OptionList(
                *[Option(label, id=str(days)) for label, days in self.OPTIONS],
                id="rec-range-list",
            )
            yield Static(
                "↑/↓ 이동 · Enter: 시작 · Esc: 취소",
                id="rec-range-hint",
            )

    def on_mount(self) -> None:
        try:
            ol = self.query_one("#rec-range-list", OptionList)
            try:
                idx = next(
                    i for i, (_, d) in enumerate(self.OPTIONS)
                    if d == self._initial_days
                )
            except StopIteration:
                idx = next(
                    i for i, (_, d) in enumerate(self.OPTIONS)
                    if d == self.DEFAULT_DAYS
                )
            ol.highlighted = idx
            ol.focus()
        except Exception:  # pragma: no cover
            pass

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        if event.option.id is None:
            return
        try:
            days = int(event.option.id)
        except (TypeError, ValueError):  # pragma: no cover
            self.dismiss(None)
            return
        self.last_choice = days
        self.dismiss(days)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RecurringScanProgressModal(ModalScreen[None]):
    """반복거래누락 검사 fetch/분석 중 본 화면을 가리는 진행 popup.

    BINDINGS 없음 → 작업 완료 전 사용자가 닫지 못함 (ScanProgressModal 과
    같은 정책).
    """

    DEFAULT_CSS = """
    RecurringScanProgressModal {
        align: center middle;
    }
    #rec-progress-frame {
        width: auto;
        min-width: 50;
        max-width: 70;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #rec-progress-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #rec-progress-activity {
        height: auto;
        min-height: 2;
        margin-top: 1;
        content-align: center middle;
    }
    #rec-progress-hint {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self, initial: str = "준비 중…") -> None:
        super().__init__()
        self._activity_text: str = initial
        self.last_activity: str = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="rec-progress-frame"):
            yield Static("[bold]🔁 반복 거래 누락 검사 중[/bold]",
                         id="rec-progress-title")
            yield Static(self._activity_text, id="rec-progress-activity")
            yield Static("잠시만 기다려주세요…", id="rec-progress-hint")

    def set_activity(self, text: str) -> None:
        self._activity_text = text
        self.last_activity = text
        try:
            self.query_one("#rec-progress-activity", Static).update(text)
        except Exception:  # pragma: no cover — not mounted yet.
            pass


class RecurringOmissionScreen(ModalScreen[bool]):
    """누락 있는 반복 시리즈 목록 + 회차 상세 + 처리/무시."""

    DEFAULT_CSS = """
    RecurringOmissionScreen {
        align: center middle;
    }
    #rec-frame {
        width: 95%;
        max-width: 150;
        min-width: 70;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #rec-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #rec-summary {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #rec-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #rec-table {
        height: auto;
        max-height: 16;
        margin-top: 1;
    }
    #rec-detail {
        height: auto;
        min-height: 2;
        margin-top: 1;
        padding: 0 1;
        border-top: tall $accent-darken-2;
    }
    #rec-status {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #rec-status.error {
        color: $error;
    }
    #rec-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #rec-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "닫기", show=True),
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        Binding("enter", "show_detail", "상세", show=True),
        Binding("h", "mark_handled", "처리함 (h)", show=True),
        *bind_ko("h", "mark_handled", "처리함"),
        Binding("d", "mark_dismissed", "무시 (d)", show=True),
        *bind_ko("d", "mark_dismissed", "무시"),
        Binding("f5", "refresh", "새로고침 (F5)", show=True, priority=True),
        Binding("ctrl+r", "refresh", "새로고침", show=False, priority=True),
    ]

    def __init__(
        self,
        series: list[StoredSeries],
        *,
        repo: RecurringScanRepository,
        section_id: str,
        range_start: str,
        range_end: str,
        session: SessionState,
        refresh_callback: RefreshCallback,
        cached: bool = False,
    ) -> None:
        super().__init__()
        self._series = list(series)
        self._repo = repo
        self._section_id = section_id
        self._range_start = range_start
        self._range_end = range_end
        self._session = session
        self._refresh_callback = refresh_callback
        self._cached = cached
        self._dirty = False
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="rec-frame"):
            yield Static("[bold]반복 거래 누락 검사 — 결과[/bold]", id="rec-title")
            yield Static("", id="rec-summary")
            yield Static(
                "↑/↓ 이동 · Enter: 누락 회차 상세 · h: 처리함 · d: 무시 · "
                "F5: 새로고침 · Esc: 닫기",
                id="rec-hint",
            )
            yield DataTable(id="rec-table", cursor_type="row")
            yield Static("", id="rec-detail")
            yield Static("", id="rec-status")
            with Horizontal(id="rec-buttons"):
                yield Button("처리함 (h)", id="rec-btn-handled", variant="success")
                yield Button("무시 (d)", id="rec-btn-dismissed")
                yield Button("새로고침 (F5)", id="rec-btn-refresh")
                yield Button("닫기 (Esc)", id="rec-btn-close")

    def on_mount(self) -> None:
        self._render_all()
        try:
            self.query_one("#rec-table", DataTable).focus()
        except Exception:  # pragma: no cover
            pass

    # ---- rendering -----------------------------------------------------

    def _render_all(self) -> None:
        self._render_summary()
        self._render_table()
        self._render_detail()

    def _render_summary(self) -> None:
        total = len(self._series)
        pending = sum(1 for s in self._series if s.status == "pending")
        handled = sum(1 for s in self._series if s.status == "handled")
        dismissed = sum(1 for s in self._series if s.status == "dismissed")
        cache_hint = " · 💾 sqlite 캐시" if self._cached else " · 🌐 후잉에서 fetch"
        try:
            self.query_one("#rec-summary", Static).update(
                f"범위: [b]{self._range_start} ~ {self._range_end}[/b]"
                f"{cache_hint}\n"
                f"누락 시리즈 [b]{total}[/b] · 처리됨 [green]{handled}[/green] · "
                f"남음 [yellow]{pending}[/yellow]"
                + (f" · 무시 {dismissed}" if dismissed else "")
            )
        except Exception:
            pass

    def _render_table(self) -> None:
        try:
            table = self.query_one("#rec-table", DataTable)
        except Exception:
            return
        table.clear(columns=True)
        table.add_column("#", width=4)
        table.add_column("상태", width=10)
        table.add_column("주기", width=8)
        table.add_column("항목", width=22)
        table.add_column("누락", width=14)
        table.add_column("마지막", width=10)
        table.add_column("대표 금액", width=12)
        for i, s in enumerate(self._series, start=1):
            if s.status == "handled":
                state = "[green]✓ 처리[/green]"
            elif s.status == "dismissed":
                state = "[dim]× 무시[/dim]"
            else:
                state = "[yellow]⏳ 남음[/yellow]"
            cadence = CADENCE_LABELS_KO.get(s.cadence, s.cadence)
            n_overdue = sum(1 for m in s.missing if m.get("kind") == "overdue")
            n_gap = sum(1 for m in s.missing if m.get("kind") == "gap")
            parts: list[str] = []
            if n_overdue:
                parts.append(f"[red]연체 {n_overdue}[/red]")
            if n_gap:
                parts.append(f"[yellow]사이 {n_gap}[/yellow]")
            missing_lbl = " ".join(parts) if parts else str(len(s.missing))
            table.add_row(
                str(i), state, cadence, s.item, missing_lbl,
                str(s.last_date)[:8], _fmt_money(s.typical_money),
                key=str(s.id),
            )

    def _current(self) -> StoredSeries | None:
        if not self._series:
            return None
        try:
            table = self.query_one("#rec-table", DataTable)
            idx = max(0, table.cursor_row or 0)
        except Exception:  # pragma: no cover
            idx = 0
        if 0 <= idx < len(self._series):
            return self._series[idx]
        return None

    def _render_detail(self) -> None:
        s = self._current()
        try:
            detail = self.query_one("#rec-detail", Static)
        except Exception:
            return
        if s is None:
            detail.update("")
            return
        left = _account_title(self._session, s.l_account_id)
        right = _account_title(self._session, s.r_account_id)
        lines = [
            f"[b]{s.item}[/b]  ·  {CADENCE_LABELS_KO.get(s.cadence, s.cadence)}"
            f"  ·  {left} → {right}  ·  대표 {_fmt_money(s.typical_money)} 원"
            f"  ·  회차 {s.occurrences} 회",
            "빠진 회차:",
        ]
        for m in s.missing:
            kind = "연체" if m.get("kind") == "overdue" else "사이 누락"
            color = "red" if m.get("kind") == "overdue" else "yellow"
            lines.append(f"  · [{color}]{m.get('expected_date')}[/{color}] ({kind})")
        detail.update("\n".join(lines))

    # ---- key actions ---------------------------------------------------

    def action_close(self) -> None:
        self.dismiss(self._dirty)

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#rec-table", DataTable).action_cursor_up()
        except Exception:  # pragma: no cover
            pass
        self._render_detail()

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#rec-table", DataTable).action_cursor_down()
        except Exception:  # pragma: no cover
            pass
        self._render_detail()

    def on_data_table_row_highlighted(self, event: Any) -> None:
        self._render_detail()

    def action_show_detail(self) -> None:
        self._render_detail()

    def action_mark_handled(self) -> None:
        self._set_status_for(self._mark("handled"))

    def action_mark_dismissed(self) -> None:
        self._set_status_for(self._mark("dismissed"))

    def _mark(self, status: str) -> str:
        s = self._current()
        if s is None:
            return "선택된 시리즈가 없습니다."
        if s.status == status:
            return f"이미 '{status}' 상태입니다."
        try:
            self._repo.update_status(s.id, status)
        except Exception as e:  # pragma: no cover
            log.exception("update_status failed")
            return f"상태 변경 실패: {e}"
        s.status = status
        self._dirty = True
        self._render_summary()
        self._render_table()
        label = "처리함" if status == "handled" else "무시"
        return f"'{s.item}' → {label}."

    def action_refresh(self) -> None:
        self._set_status("🌐 후잉에서 새로고침 중…")
        self._refresh_worker()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "rec-btn-close":
            self.action_close()
        elif bid == "rec-btn-handled":
            self.action_mark_handled()
        elif bid == "rec-btn-dismissed":
            self.action_mark_dismissed()
        elif bid == "rec-btn-refresh":
            self.action_refresh()

    def _set_status_for(self, msg: str) -> None:
        self._set_status(msg)

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        self.last_status = msg
        try:
            st = self.query_one("#rec-status", Static)
            st.update(msg)
            if error:
                st.add_class("error")
            else:
                st.remove_class("error")
        except Exception:  # pragma: no cover
            pass

    @work(exclusive=True, group="recurring_refresh", name="recurring_refresh")
    async def _refresh_worker(self) -> None:
        try:
            new_series = await self._refresh_callback()
        except Exception as e:  # pragma: no cover
            log.exception("recurring refresh_callback failed")
            self._set_status(f"새로고침 실패: {e}", error=True)
            return
        self._series = list(new_series)
        self._cached = False
        self._render_all()
        n_pending = sum(1 for s in self._series if s.status == "pending")
        self._set_status(f"✅ 새로고침 완료 — 남음 {n_pending} 건.")
