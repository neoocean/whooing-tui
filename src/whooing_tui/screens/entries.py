"""EntriesScreen — 활성 섹션의 거래내역 표.

HomeScreen 에서 `e` 키로 push 한다. 진입과 동시에 최근 N일 (기본
`config.entries.default_window_days`, 30일) 의 거래를 fetch 해서 DataTable
로 표시. 100-cap pagination 위험 (`WhooingClient._list_entries_chunked` 의
주석 참고) 은 footer 에 인지 메시지로 노출.

키 바인딩:
  q / escape  HomeScreen 으로 복귀
  r           재로드 (현재 윈도우)
  +  / -      윈도우를 ±7일씩 늘리기/줄이기
  d           날짜 범위 직접 입력 (DateRangeDialog)
  ?           화면 도움말 (별도 CL 에서 추가 예정)

DataTable 컬럼:
  date  money  left  right  item  memo

money 는 천단위 콤마 + 우측 정렬. left/right 는 account_id 를 SessionState
의 양방향 인덱스로 즉시 title 로 변환 — 사용자에게는 코드 대신 이름이
보인다.
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from whooing_tui.client import WhooingClient
from whooing_tui.config import load_config
from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_tui.models import ToolError

log = logging.getLogger(__name__)


def _fmt_money(v: Any) -> str:
    """후잉 money 는 정수 (KRW). 천단위 콤마 + 음수 부호 보존."""
    if v is None or v == "":
        return ""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:,}"


class EntriesScreen(Screen):
    """활성 섹션의 거래내역 화면."""

    DEFAULT_CSS = """
    EntriesScreen {
        layers: base;
    }
    #entries-body {
        height: 1fr;
    }
    #entries-table {
        height: 1fr;
        border: round $accent;
    }
    #status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #status.error {
        color: $error;
    }
    #status.warn {
        color: $warning;
    }
    """

    BINDINGS = [
        Binding("q", "back", "Back", show=True),
        Binding("escape", "back", "Back", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("plus", "extend_window", "+7d", show=True),
        Binding("minus", "shrink_window", "-7d", show=True),
        Binding("equals_sign", "extend_window", "", show=False),  # '+' 키 (no shift)
    ]

    # 한 번에 fetch 할 거래는 후잉 server-side hard cap 100건. 단일 일자에
    # 100건 초과는 _list_entries_chunked 가 더 분할할 수 없으므로 footer
    # 에 경고 띄움 (DESIGN §4.3 + MEMORY §7).
    _SERVER_PAGE_CAP = 100

    def __init__(self, client: WhooingClient) -> None:
        super().__init__()
        self._client = client
        cfg = load_config()
        self._window_days: int = max(1, cfg.default_window_days)
        # status 평문 보관 (테스트 친화 — HomeScreen 과 동일 컨벤션)
        self.last_status: str = ""
        # 마지막 fetch 결과 메타 (테스트가 검사할 수 있도록)
        self.last_entry_count: int = 0
        self.last_cap_warning: bool = False

    # ---- compose -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="entries-body"):
            yield DataTable(id="entries-table", zebra_stripes=True, cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    # ---- mount ---------------------------------------------------------

    def on_mount(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        table.add_columns("date", "money", "left", "right", "item", "memo")
        self.set_status("거래내역 로드 중…")
        self.refresh_entries()
        table.focus()

    # ---- actions -------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self.set_status("재로드 중…")
        self.refresh_entries()

    def action_extend_window(self) -> None:
        self._window_days = min(365 * 5, self._window_days + 7)
        self.set_status(f"윈도우 +7일 → 최근 {self._window_days}일. 재로드 중…")
        self.refresh_entries()

    def action_shrink_window(self) -> None:
        self._window_days = max(1, self._window_days - 7)
        self.set_status(f"윈도우 -7일 → 최근 {self._window_days}일. 재로드 중…")
        self.refresh_entries()

    # ---- worker --------------------------------------------------------

    @work(exclusive=True, group="entries", name="refresh_entries")
    async def refresh_entries(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        section_id = session.section_id
        if not section_id:
            self.set_status("활성 섹션이 없습니다 — 홈으로 돌아가 섹션을 선택하세요.", error=True)
            return

        end_date = today_yyyymmdd()
        start_date = days_ago_yyyymmdd(self._window_days - 1)
        try:
            entries = await self._client.list_entries(section_id, start_date, end_date)
        except ToolError as e:
            self.set_status(f"거래내역 로드 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("entries refresh failed")
            self.set_status(f"거래내역 로드 실패 (INTERNAL): {e}", error=True)
            return

        # 후잉 응답은 보통 최근 → 과거 순. 사용자에게도 같은 순서로 보여준다.
        # entry_date desc, 같은 날짜는 entry_id desc (있다면) 로 보조 정렬.
        entries_sorted = sorted(
            entries,
            key=lambda e: (e.get("entry_date") or "", str(e.get("entry_id") or "")),
            reverse=True,
        )

        self.last_entry_count = len(entries_sorted)
        self._render_table(entries_sorted)
        self._update_window_status(start_date, end_date, entries_sorted)

    # ---- render --------------------------------------------------------

    def _render_table(self, entries: list[dict[str, Any]]) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        table = self.query_one("#entries-table", DataTable)
        table.clear()
        for e in entries:
            date_s = e.get("entry_date") or ""
            money_s = _fmt_money(e.get("money"))
            l_id = e.get("l_account_id") or ""
            r_id = e.get("r_account_id") or ""
            l_name = session.title_of(l_id) if l_id else ""
            r_name = session.title_of(r_id) if r_id else ""
            item = e.get("item") or ""
            memo = e.get("memo") or ""
            table.add_row(date_s, money_s, l_name, r_name, item, memo)

    def _update_window_status(
        self,
        start_date: str,
        end_date: str,
        entries: list[dict[str, Any]],
    ) -> None:
        n = len(entries)
        # 100-cap 경고: 단일 일자에 100건이 모인 entries 가 있으면 누락 가능성.
        # entries 응답은 entry_date 별로 cluster 가능 — 같은 date 가 정확히
        # _SERVER_PAGE_CAP 개면 그 일자가 cap 도달 가능성을 의심한다.
        per_date: dict[str, int] = {}
        for e in entries:
            d = e.get("entry_date") or ""
            per_date[d] = per_date.get(d, 0) + 1
        cap_dates = [d for d, c in per_date.items() if c >= self._SERVER_PAGE_CAP]
        self.last_cap_warning = bool(cap_dates)

        msg = (
            f"{n}건 표시 ({start_date} ~ {end_date}, 최근 {self._window_days}일, "
            f"section={self.app.session.section_id})"  # type: ignore[attr-defined]
        )
        if self.last_cap_warning:
            cap_list = ", ".join(cap_dates[:3]) + (" …" if len(cap_dates) > 3 else "")
            msg += f"  ⚠ 100-cap 도달 가능 ({cap_list})"
            self.set_status(msg, warn=True)
        else:
            self.set_status(msg)

    # ---- status bar ----------------------------------------------------

    def set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
