"""EntriesScreen — TUI 의 **초기 화면** (CL #51023+).

앱이 시작되면 본 화면이 첫 push 된다. 진입 시점에 자체적으로 sections /
accounts / entries 를 chain 으로 부팅한다 (별도 HomeScreen 없이):

  1. SessionState.section_id 가 비어있으면 sections-list 호출 → WHOOING_
     SECTION_ID 환경변수 우선, 없으면 첫 섹션을 자동 활성화.
  2. SessionState.accounts_flat 이 비어있으면 accounts-list 호출 → 양방향
     인덱스 빌드.
  3. 최근 N일 (기본 `config.entries.default_window_days`, 30) 거래 fetch.

100-cap pagination 위험은 footer 의 `.warn` 클래스로 인지 메시지를 노출.

별도 옵션 화면 (CL #51023):
  s   SectionPickerScreen 으로 push — 섹션 선택 후 dismiss → 자동 재부팅.
  a   AccountsScreen 으로 push — 계정과목 조회 / 추가 / 수정 / 삭제.
      돌아온 후 자동으로 entries 재로드 (cache 가 invalidate 됐을 수 있음).

키 바인딩 (Footer 가 표시):
  q / escape       앱 종료 (initial screen 이므로 pop 이 아닌 exit).
  r                재로드 (현재 윈도우, 캐시 강제 invalidate).
  + / -            윈도우 ±7일.
  s / a            섹션 picker / 계정과목 화면 push.
  n / Enter / d    거래 추가 / 수정 / 삭제 (EntryEditDialog / Confirm).
  ?                화면 도움말 (HelpModal).

DataTable 컬럼:
  date  money  left  right  item  memo

money 는 천단위 콤마. left/right 는 account_id 를 SessionState 의 양방향
인덱스로 즉시 title 로 변환 — 사용자에게는 코드 대신 이름이 보인다.
"""

from __future__ import annotations

import logging
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from whooing_core import db as core_db

from whooing_tui import data as tui_data
from whooing_tui.client import WhooingClient
from whooing_tui.config import load_config
from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_tui.filters import FILTERABLE_COLUMNS, filter_entries
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.screens.edit_entry import (
    ConfirmModal, EntryDraft, EntryEditDialog,
)
from whooing_tui.state import (
    default_section_id_from_env,
    load_last_section_id,
    save_last_section_id,
)

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


def _fmt_date(v: Any) -> str:
    """후잉 entry_date (YYYYMMDD 8자리) 를 YYYY-MM-DD 표시용으로 정규화.

    후잉 응답은 가끔 `"20260510.0001"` 처럼 sub-index (sequence) 가 붙어
    오므로 `.` 앞 8자리만 사용. 8자리 숫자가 아니면 손대지 않고 그대로
    반환 (디버깅 친화).
    """
    if v is None or v == "":
        return ""
    s = str(v)
    head = s.split(".", 1)[0]
    if len(head) == 8 and head.isdigit():
        return f"{head[:4]}-{head[4:6]}-{head[6:8]}"
    return s


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

    # 영문 letter key 는 한글 IME 일 때도 동작하도록 `bind_ko` 로 자모
    # binding 을 같이 등록 (CL #51041). escape / enter / question_mark /
    # plus / minus / equals_sign 은 IME 영향이 없으므로 그대로.
    #
    # CL #51053+: 좌우 방향키로 컬럼 navigation, Enter 의 의미가 column
    # 별 컨텍스트 액션 (date/left/right/item = 필터 적용, money/memo = 거래
    # 수정 dialog). 필터 활성 상태에서 'r' 또는 'c' 로 해제.
    # CL #51064 부터: 종료 = `q` 만. `escape` 는 컬럼 marker 해제 전용
    # (marker 가 없는 초기 상태에서는 noop). 사용자 지시: "ESC로 종료되지
    # 않게 해주세요. 종료키는 q 입니다."
    BINDINGS = [
        *bind_ko("q", "back", "Quit", show=True),
        Binding("escape", "deactivate_column", "Cancel col", show=False),
        *bind_ko("s", "open_sections", "Sections", show=True, priority=True),
        *bind_ko("a", "open_accounts", "Accounts", show=True, priority=True),
        *bind_ko("n", "new_entry", "New", show=True, priority=True),
        # Enter 의 의미는 _column_active + _active_col 에 따라 분기.
        Binding("enter", "context_enter", "Enter", show=True, priority=True),
        *bind_ko("e", "edit_entry", "Edit", show=True, priority=True),
        *bind_ko("d", "delete_entry", "Delete", show=True, priority=True),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("c", "clear_filter", "Clear", show=True, priority=True),
        Binding("left", "prev_column", "←", show=False, priority=True),
        Binding("right", "next_column", "→", show=False, priority=True),
        # ↑/↓ 는 default DataTable 의 cursor 이동을 가로채서 sentinel
        # 토글까지 처리한다 (CL #51074+).
        Binding("up", "row_up", "↑", show=False, priority=True),
        Binding("down", "row_down", "↓", show=False, priority=True),
        Binding("question_mark", "help", "Help", show=True, priority=True, key_display="?"),
        Binding("plus", "extend_window", "+7d", show=True),
        Binding("minus", "shrink_window", "-7d", show=True),
        Binding("equals_sign", "extend_window", "", show=False),  # '+' 키 (no shift)
    ]

    # DataTable 의 컬럼 순서 — _active_col index 와 1:1 매핑. _render_table /
    # add_column 호출 순서와 일치해야 한다.
    _COLUMN_NAMES: tuple[str, ...] = (
        "date", "money", "left", "right", "item", "memo",
    )

    # 활성 cell 시각 마커 — Rich markup 으로 cell 의 background 색을 변경.
    # cursor_type="row" 의 default 색 (보통 파란/accent) 와 구분되도록 노란
    # 배경 + 검정 글자. textual 의 색 변수 (`$warning` 등) 가 markup 안에서
    # 변수 치환되지 않을 수 있어 안전하게 명시 색.
    _ACTIVE_CELL_STYLE = "black on yellow"

    # CL #51072 부터: DataTable 의 row 0 은 "새 거래 추가" sentinel.
    # cursor 가 row 0 일 때 enter = action_new_entry. 실 거래는 row 1+ 부터.
    # sentinel 의 첫 column 텍스트 — 사용자가 cursor 를 올렸을 때 무엇을
    # 하는 자리인지 즉시 알 수 있도록. 다른 column 은 빈 cell.
    _NEW_ENTRY_SENTINEL_LABEL = "[+ 새 거래 추가]"

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
        # 표시 중인 entries — DataTable row index ↔ entry dict 1:1 매핑.
        # 필터가 활성이면 _all_entries 의 부분집합. 비활성이면 동일 list.
        # 사용자가 row 를 선택하면 entry_id / 기존 값을 dialog 로 prefill
        # 할 수 있도록.
        self._entries: list[dict[str, Any]] = []
        # 필터 적용 전 원본 (refresh_entries 가 받은 그대로). 필터 해제
        # ('r' / 'c') 시 이 list 로 복원.
        self._all_entries: list[dict[str, Any]] = []
        # 활성 필터 ((column, target_entry) 또는 None). 사용자에게 status
        # 로 안내 + 같은 컬럼에서 다시 enter 시 toggle 같은 후속 정책에
        # 활용 가능.
        self._active_filter: tuple[str, dict[str, Any]] | None = None
        # 좌우 방향키로 이동하는 컬럼 인덱스 (CL #51053+). DataTable 의
        # cursor_type 이 "row" 라 textual 자체로는 column 추적이 안 된다 —
        # 화면이 직접 관리.
        self._active_col: int = 0
        # CL #51064 부터: 컬럼 marker 의 활성/비활성 상태 분리. 초기는
        # False — 거래 row 의 파란 cursor 만 보이고 노란 cell marker 는
        # 없다. ←/→ 첫 누름 시 True 로 (marker 등장), Esc 로 다시 False.
        self._column_active: bool = False
        # 마지막으로 마커링한 cell 좌표 — _column_active 가 True 인 동안만
        # 의미. False 일 때 None.
        self._marked_cell: tuple[int, int] | None = None
        # CL #51074 부터: sentinel row 의 가시성. 평소엔 False (숨김).
        # 거래 목록 맨 위 (cursor row 0) 에서 ↑ 한 번 더 누르면 True 로 —
        # sentinel 이 row 0 으로 등장하고 cursor 도 sentinel 로 이동.
        # sentinel 에서 ↓ 누르면 다시 False, sentinel 사라지고 cursor 가
        # 첫 실거래로. 빈 entries 일 때는 강제 True (사용자 진입점 보장).
        self._show_sentinel: bool = False

    # ---- compose -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="entries-body"):
            yield DataTable(id="entries-table", zebra_stripes=True, cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    # ---- mount ---------------------------------------------------------

    def on_mount(self) -> None:
        # 로컬 sqlite (annotations / hashtags) 스키마 보장 — 첫 실행이거나
        # 마이그레이션 직후라도 entry edit 시 db_path 가 비어있을 수 있다.
        # init_schema 는 멱등이므로 매 mount 마다 호출해도 안전.
        try:
            tui_data.init_shared_schema()
        except Exception:  # pragma: no cover
            log.exception("init_shared_schema failed; 로컬 메모/해시태그 비활성화")

        table = self.query_one("#entries-table", DataTable)
        # 컬럼별 width — `left` 는 사용자 요청 (CL #51051) 으로 12 cells 로
        # fixed (한글 계정과목명 6자 + 약간의 여유). 그 이상은 textual 의
        # 자동 ellipsis. `right` 는 자동 — 차변/대변 의 시각 비대칭이
        # 사용자 의도였다.
        table.add_column("date")
        table.add_column("money")
        table.add_column("left", width=12)
        table.add_column("right")
        table.add_column("item")
        table.add_column("memo")
        self.set_status("거래내역 로드 중…")
        self.refresh_entries()
        table.focus()

    # ---- actions -------------------------------------------------------

    def action_back(self) -> None:
        """초기 화면이라 pop 대신 앱 종료."""
        self.app.exit()

    def action_help(self) -> None:
        """현재 화면의 BINDINGS 를 모달로 보여줌."""
        from whooing_tui.screens.help import HelpModal
        self.app.push_screen(HelpModal("EntriesScreen", list(self.BINDINGS)))

    def action_open_sections(self) -> None:
        """섹션 picker 모달 push. 사용자가 다른 섹션을 고르면 자동 재로드."""
        from whooing_tui.screens.sections import SectionPickerScreen
        session = self.app.session  # type: ignore[attr-defined]

        def _on_close(result: tuple[str, str | None] | None) -> None:
            if result is None:
                return
            sid, title = result
            if sid == session.section_id:
                # 같은 섹션 — 그대로 둔다.
                return
            session.set_section(sid, title)
            # 사용자 명시 선택은 영구 저장 — 다음 부팅 시 복원.
            save_last_section_id(sid)
            self.set_status(
                f"섹션 {sid} ({title or '?'}) 으로 전환. 재로드 중…",
            )
            self.refresh_entries()

        self.app.push_screen(
            SectionPickerScreen(
                self._client, current_section_id=session.section_id,
            ),
            _on_close,
        )

    def action_open_accounts(self) -> None:
        """계정과목 화면 push. 돌아온 후 자동으로 entries 재로드.

        AccountsScreen 안의 mutation 이 cache 를 invalidate 하므로 단순히
        refresh_entries() 만 부르면 fresh 한 결과가 나온다.
        """
        from whooing_tui.screens.accounts import AccountsScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True)
            return

        def _on_close(_: Any) -> None:
            self.set_status("계정과목 화면에서 돌아왔습니다. 재로드 중…")
            self.refresh_entries()

        self.app.push_screen(AccountsScreen(self._client), _on_close)

    def action_refresh(self) -> None:
        # 사용자가 'r' = "지금 즉시 후잉 데이터" — 캐시가 있으면 invalidate.
        # 필터도 자연스럽게 해제 (full fetch 라 _all_entries 가 재구성됨).
        session = self.app.session  # type: ignore[attr-defined]
        invalidate = getattr(self._client, "invalidate_section", None)
        if session.section_id and callable(invalidate):
            invalidate(session.section_id)
        self._active_filter = None
        self.set_status("재로드 중…")
        self.refresh_entries()

    def action_clear_filter(self) -> None:
        """필터 해제 — _all_entries 를 그대로 다시 표시 (재로드 X)."""
        if self._active_filter is None:
            self.set_status("활성 필터 없음.")
            return
        self._active_filter = None
        self._entries = list(self._all_entries)
        self._render_table(self._entries)
        self._update_window_status_after_filter_clear()

    # ---- row navigation + sentinel 토글 (CL #51074+) ------------------

    def action_row_up(self) -> None:
        """↑ — 거래 목록 맨 위 row 에서 한 번 더 누르면 sentinel 등장.

        그 외에는 default DataTable cursor 이동에 위임. 토글 직후 cursor
        는 새 sentinel (= row 0) 로 이동 — 사용자 시야상 위로 한 칸 이동.
        """
        table = self.query_one("#entries-table", DataTable)
        cur = table.cursor_row
        first_entry_row = 1 if self._show_sentinel else 0
        if (
            self._entries
            and not self._show_sentinel
            and cur == first_entry_row  # 거래 목록 맨 위 = row 0 (숨김 상태)
        ):
            # sentinel 표시 토글 + cursor 가 새 sentinel (row 0) 로
            self._show_sentinel = True
            self._render_table(self._entries, target_cursor=0)
            return
        # 그 외 — default cursor up 위임
        try:
            table.action_cursor_up()
        except Exception:  # pragma: no cover — boundary 등
            pass

    def action_row_down(self) -> None:
        """↓ — sentinel row (0) 에서 누르면 sentinel 숨김 + cursor 가 첫
        실거래로. 그 외엔 default cursor 이동."""
        table = self.query_one("#entries-table", DataTable)
        cur = table.cursor_row
        if self._show_sentinel and cur == 0 and self._entries:
            # sentinel 사라지면 entries 가 row 0+ 부터 시작 — cursor 를 row 0
            # 으로 (= 이전 row 1 의 첫 실거래).
            self._show_sentinel = False
            self._render_table(self._entries, target_cursor=0)
            return
        try:
            table.action_cursor_down()
        except Exception:  # pragma: no cover
            pass

    # ---- 컬럼 navigation (CL #51053+, 활성/비활성 상태는 #51064+) -----

    def action_prev_column(self) -> None:
        """← 키 — marker 비활성이면 활성화만 (_active_col 그대로), 활성이면 -1."""
        if not self._column_active:
            self._column_active = True
            self._update_active_cell_marker()
            self._announce_active_column()
            return
        if self._active_col > 0:
            self._active_col -= 1
            self._update_active_cell_marker()
            self._announce_active_column()

    def action_next_column(self) -> None:
        """→ 키 — marker 비활성이면 활성화만, 활성이면 +1."""
        if not self._column_active:
            self._column_active = True
            self._update_active_cell_marker()
            self._announce_active_column()
            return
        if self._active_col < len(self._COLUMN_NAMES) - 1:
            self._active_col += 1
            self._update_active_cell_marker()
            self._announce_active_column()

    def action_deactivate_column(self) -> None:
        """Esc — 활성 컬럼 marker + 활성 필터를 함께 해제. 둘 다 비활성이면
        noop (앱 종료 X).

        사용자 지시:
          * CL #51064: "ESC를 누르면 오렌지색 커서만 선택취소… 파란색
            커서만 있는 상태에서 ESC는 아무 동작도 하지 않습니다. ESC로
            종료되지 않게 해주세요. 종료키는 q 입니다."
          * CL #51068: "오렌지색 커서로 필터링이 적용된 상태에서 ESC 키
            를 누르면 커서 하이라이트 해제 및 동시에 필터도 해제되어야
            합니다."

        결합 정의: 둘 다 활성 → 둘 다 해제. 한 쪽만 활성 → 그것만 해제.
        둘 다 비활성 → noop (사용자 명시 — 앱 종료 X).
        """
        if not self._column_active and self._active_filter is None:
            return  # noop — 둘 다 비활성

        had_filter = self._active_filter is not None

        if self._column_active:
            self._column_active = False
            self._update_active_cell_marker()  # marker cleanup

        if had_filter:
            self._active_filter = None
            self._entries = list(self._all_entries)
            # _render_table 끝의 _update_active_cell_marker 가 _column_active
            # 비활성이라 marker 재적용 X — 깨끗한 plain table 로 그려진다.
            self._render_table(self._entries)

        # status 안내 — 어떤 게 해제됐는지 명시.
        if had_filter:
            self.set_status("컬럼 선택 / 필터 해제 — ←/→ 다시 눌러 재활성.")
        else:
            self.set_status("컬럼 선택 해제 — ←/→ 다시 눌러 재활성.")

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted,
    ) -> None:
        """↑/↓ 또는 click 으로 cursor row 가 바뀌면 marker 도 따라 이동.

        sentinel row (0) 에 cursor 가 가면 marker 가 자동으로 cleanup
        되고, status 에 "Enter = 새 거래 추가" 안내. `_column_active=False`
        이면 _update_active_cell_marker 가 알아서 early return.
        """
        self._update_active_cell_marker()
        if self._is_on_sentinel_row():
            self.set_status("[Enter = 새 거래 추가]")

    def _update_active_cell_marker(self) -> None:
        """marker 상태와 cell content 를 동기화. sentinel-aware (CL #51074+).

        sentinel row 가 보이면 그 자리 (row 0) 는 항상 plain — marker 안
        적용. entry index 변환은 `_entry_index_for_row` 가 책임.
        """
        table = self.query_one("#entries-table", DataTable)

        # 이전 marker cell 복원. _column_active 와 무관하게 항상 먼저.
        if self._marked_cell is not None:
            prev_row, prev_col = self._marked_cell
            prev_entry_idx = self._entry_index_for_row(prev_row)
            if (
                prev_entry_idx is not None
                and 0 <= prev_col < len(self._COLUMN_NAMES)
            ):
                plain_prev = self._format_cell(
                    self._entries[prev_entry_idx], prev_col,
                )
                try:
                    table.update_cell_at(
                        Coordinate(prev_row, prev_col),
                        plain_prev,
                        update_width=False,
                    )
                except Exception:  # pragma: no cover — coordinate stale
                    pass
            self._marked_cell = None

        # 비활성이거나 entries 비어있으면 새 marker 적용 X.
        if not self._column_active or not self._entries:
            return

        cur_row = table.cursor_row
        cur_entry_idx = self._entry_index_for_row(cur_row)
        if cur_entry_idx is None:
            # sentinel row 또는 boundary 외 — marker 안 적용.
            return
        cur_col = self._active_col

        plain_cur = self._format_cell(self._entries[cur_entry_idx], cur_col)
        # CL #51087+: money 컬럼은 Rich Text (justify="right") 로 와서 markup
        # 래핑 시 정렬 정보가 보존되지 않는다 — Text 면 stylize 로 같은 색을
        # 주고, str 이면 markup 그대로.
        if isinstance(plain_cur, Text):
            marked: Any = plain_cur.copy()
            marked.stylize(self._ACTIVE_CELL_STYLE)
        else:
            marker_text = plain_cur if plain_cur else " "  # 빈 cell 도 보이게
            marked = f"[{self._ACTIVE_CELL_STYLE}]{marker_text}[/]"
        try:
            table.update_cell_at(
                Coordinate(cur_row, cur_col),
                marked,
                update_width=False,
            )
        except Exception:  # pragma: no cover
            return
        self._marked_cell = (cur_row, cur_col)

    def _announce_active_column(self) -> None:
        """status bar 에 현재 컬럼 + Enter 시 동작 안내."""
        col = self._COLUMN_NAMES[self._active_col]
        if col in FILTERABLE_COLUMNS:
            hint = f"Enter = 같은 {col} 으로 필터"
        elif col in ("money", "memo"):
            hint = "Enter = 거래 수정"
        else:
            hint = ""  # unreachable
        self.set_status(f"활성 컬럼: {col}    {hint}")

    def action_context_enter(self) -> None:
        """Enter — sentinel / 컬럼 marker 활성 여부에 따라 분기.

        - **sentinel row** (cursor row 0, CL #51072+): 새 거래 추가 dialog.
        - **실 거래 + 컬럼 비활성** (파란 row cursor 만): 거래 수정 dialog.
        - **실 거래 + 컬럼 활성** (파란 + 노란 cell marker):
          * date / left / right / item: 같은 값으로 필터.
          * money / memo: 거래 수정 dialog.
        """
        # Sentinel row 우선 — entry 가 없는 자리.
        if self._is_on_sentinel_row():
            self.action_new_entry()
            return

        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        # 컬럼 비활성 → 항상 edit.
        if not self._column_active:
            self.action_edit_entry()
            return
        col = self._COLUMN_NAMES[self._active_col]
        if col in FILTERABLE_COLUMNS:
            self._apply_filter(col, target)
        else:
            # money / memo 컬럼 → edit_entry.
            self.action_edit_entry()

    def _apply_filter(self, column: str, target: dict[str, Any]) -> None:
        filtered = filter_entries(self._all_entries, column, target)
        if not filtered:
            self.set_status(
                f"'{column}' 필터 — 매칭 0건 (target 의 키 정보가 없거나 결과 없음). "
                f"c 로 해제 / r 로 재로드.",
                warn=True,
            )
            return
        self._active_filter = (column, target)
        self._entries = filtered
        self._render_table(filtered)
        # 필터 결과 안내. _update_window_status 는 _all_entries 윈도우 기준
        # 이라 의미가 다르다 — 필터 전용 message.
        label = self._filter_label(column, target)
        self.set_status(
            f"필터: {label} — {len(filtered)}/{len(self._all_entries)}건. "
            f"c 로 해제 / r 로 재로드.",
            warn=True,
        )

    @staticmethod
    def _filter_label(column: str, target: dict[str, Any]) -> str:
        if column == "date":
            from whooing_tui.filters import date_head
            return f"date={date_head(target.get('entry_date'))}"
        if column == "left":
            return f"left={target.get('l_account_id') or '?'}"
        if column == "right":
            return f"right={target.get('r_account_id') or '?'}"
        if column == "item":
            from whooing_tui.filters import outside_paren_keywords
            keys = outside_paren_keywords(target.get("item"))
            return f"item∋{{{', '.join(sorted(keys))}}}"
        return column

    def _update_window_status_after_filter_clear(self) -> None:
        """필터 해제 직후 status — 윈도우 정보 재계산."""
        # `start_date` / `end_date` 는 마지막 fetch 시각에 의존. 단순 안내.
        n = len(self._entries)
        section_id = self.app.session.section_id  # type: ignore[attr-defined]
        section_title = self.app.session.section_title  # type: ignore[attr-defined]
        sec_label = (
            f"{section_title} ({section_id})" if section_title else str(section_id)
        )
        self.set_status(
            f"필터 해제 — {n}건 (section={sec_label}, 최근 {self._window_days}일)"
        )

    def action_extend_window(self) -> None:
        self._window_days = min(365 * 5, self._window_days + 7)
        self.set_status(f"윈도우 +7일 → 최근 {self._window_days}일. 재로드 중…")
        self.refresh_entries()

    def action_shrink_window(self) -> None:
        self._window_days = max(1, self._window_days - 7)
        self.set_status(f"윈도우 -7일 → 최근 {self._window_days}일. 재로드 중…")
        self.refresh_entries()

    # ---- new / edit / delete -----------------------------------------

    def _entry_index_for_row(self, row: int | None) -> int | None:
        """DataTable row index → `_entries` 의 index. sentinel row 이거나
        out-of-range 면 None.

        sentinel 가시성 (`_show_sentinel`) 에 따라 +1 shift 가 동적:
          - sentinel 표시: row 0 = sentinel, row N → entries[N-1]
          - sentinel 숨김: row N → entries[N]
        """
        if row is None:
            return None
        if self._show_sentinel:
            if row < 1:
                return None
            idx = row - 1
        else:
            if row < 0:
                return None
            idx = row
        if idx >= len(self._entries):
            return None
        return idx

    def _selected_entry(self) -> dict[str, Any] | None:
        """현재 DataTable cursor 가 가리키는 entry. sentinel row 또는
        선택 불가 상태면 None.
        """
        if not self._entries:
            return None
        table = self.query_one("#entries-table", DataTable)
        idx = self._entry_index_for_row(table.cursor_row)
        if idx is None:
            return None
        return self._entries[idx]

    def _is_on_sentinel_row(self) -> bool:
        """cursor 가 sentinel row 인지 — `_show_sentinel=True` 이고 row 0."""
        if not self._show_sentinel:
            return False
        table = self.query_one("#entries-table", DataTable)
        return table.cursor_row == 0

    def action_new_entry(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id or not session.accounts_flat:
            self.set_status("계정과목 캐시가 비어있습니다 — 홈에서 섹션을 다시 활성화하세요.", error=True)
            return

        def _on_close(draft: EntryDraft | None) -> None:
            if draft is None:
                self.set_status("입력 취소됨.")
                return
            self._submit_create(draft)

        # CL #51080+: TagsPickerScreen 의 추천/자주 쓰는 태그 출처.
        existing = {"_all_tags_db": self._fetch_all_tags_db()}
        self.app.push_screen(
            EntryEditDialog(session, existing=existing), _on_close,
        )

    def action_edit_entry(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        if not target.get("entry_id"):
            self.set_status("이 거래에는 entry_id 가 없습니다 — 수정 불가.", error=True)
            return

        def _on_close(draft: EntryDraft | None) -> None:
            if draft is None:
                self.set_status("수정 취소됨.")
                return
            self._submit_update(draft)

        # 로컬 sqlite 의 해시태그를 prefill 해서 dialog 에 넘긴다 — annotation
        # 자체는 후잉 memo 와 동일하므로 별도 fetch 불필요. CL #51080+ 부터는
        # TagsPickerScreen 의 추천/자주 쓰는 태그 출처도 함께 (`_all_tags_db`).
        local_tags = self._fetch_local_tags(target.get("entry_id") or "")
        existing = dict(target)
        existing["_local_tags"] = local_tags
        existing["_all_tags_db"] = self._fetch_all_tags_db()
        self.app.push_screen(EntryEditDialog(session, existing=existing), _on_close)

    def action_delete_entry(self) -> None:
        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        eid = target.get("entry_id")
        if not eid:
            self.set_status("이 거래에는 entry_id 가 없습니다 — 삭제 불가.", error=True)
            return

        # 사용자에게 거래 요약을 보여주고 y 로 확정.
        session = self.app.session  # type: ignore[attr-defined]
        l_name = session.title_of(target.get("l_account_id") or "")
        r_name = session.title_of(target.get("r_account_id") or "")
        msg = (
            f"이 거래를 영구 삭제할까요?\n\n"
            f"  date  : {target.get('entry_date') or ''}\n"
            f"  money : {_fmt_money(target.get('money'))}\n"
            f"  left  : {l_name}\n"
            f"  right : {r_name}\n"
            f"  item  : {target.get('item') or ''}\n\n"
            f"되돌릴 수 없습니다."
        )

        def _on_close(yes: bool | None) -> None:
            # ConfirmModal 은 bool 만 dismiss 하지만 escape 로 닫히면 None
            # 일 수도 있어 안전하게 truth check.
            if not yes:
                self.set_status("삭제 취소됨.")
                return
            self._submit_delete(target)

        self.app.push_screen(ConfirmModal(msg, title="거래 삭제 확인"), _on_close)

    # ---- mutation workers --------------------------------------------

    @work(exclusive=True, group="mutate", name="create_entry")
    async def _submit_create(self, draft: EntryDraft) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        # picker 가 type 을 직접 채워주므로 그것을 우선 사용; 신뢰할 수 없으면
        # SessionState fallback.
        l_type = draft.l_type or self._account_type(draft.l_account_id)
        r_type = draft.r_type or self._account_type(draft.r_account_id)
        if not l_type or not r_type:
            self.set_status("계정 type 조회 실패 — accounts-list 를 다시 받으세요.", error=True)
            return
        try:
            response = await self._client.create_entry(
                section_id=session.section_id,
                l_account=l_type,
                l_account_id=draft.l_account_id,
                r_account=r_type,
                r_account_id=draft.r_account_id,
                money=draft.money,
                item=draft.item,
                memo=draft.memo,
                entry_date=draft.entry_date,
            )
        except ToolError as e:
            self.set_status(f"거래 생성 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("create_entry failed")
            self.set_status(f"거래 생성 실패 (INTERNAL): {e}", error=True)
            return
        # 후잉 응답에서 entry_id 를 회수해 로컬 db 에 memo + 해시태그 저장.
        new_eid = self._extract_entry_id(response)
        if new_eid and (draft.memo or draft.tags):
            self._persist_local(
                entry_id=new_eid,
                section_id=session.section_id,
                memo=draft.memo,
                tags=draft.tags,
            )
        self.set_status("거래 생성 완료. 재로드 중…")
        self.refresh_entries()

    @work(exclusive=True, group="mutate", name="update_entry")
    async def _submit_update(self, draft: EntryDraft) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not draft.entry_id:
            self.set_status("entry_id 가 없습니다 — 수정 불가.", error=True)
            return
        l_type = draft.l_type or self._account_type(draft.l_account_id)
        r_type = draft.r_type or self._account_type(draft.r_account_id)
        try:
            await self._client.update_entry(
                section_id=session.section_id,
                entry_id=draft.entry_id,
                l_account=l_type,
                l_account_id=draft.l_account_id,
                r_account=r_type,
                r_account_id=draft.r_account_id,
                money=draft.money,
                item=draft.item,
                memo=draft.memo,
                entry_date=draft.entry_date,
            )
        except ToolError as e:
            self.set_status(f"거래 수정 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("update_entry failed")
            self.set_status(f"거래 수정 실패 (INTERNAL): {e}", error=True)
            return
        self._persist_local(
            entry_id=str(draft.entry_id),
            section_id=session.section_id,
            memo=draft.memo,
            tags=draft.tags,
        )
        self.set_status("거래 수정 완료. 재로드 중…")
        self.refresh_entries()

    @work(exclusive=True, group="mutate", name="delete_entry")
    async def _submit_delete(self, target: dict[str, Any]) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        eid = target.get("entry_id")
        if not eid:
            return
        try:
            await self._client.delete_entry(
                section_id=session.section_id, entry_id=str(eid),
            )
        except ToolError as e:
            self.set_status(f"거래 삭제 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("delete_entry failed")
            self.set_status(f"거래 삭제 실패 (INTERNAL): {e}", error=True)
            return
        # 로컬 db 의 annotation/해시태그도 함께 정리 — 후잉에서 사라진 거래
        # 의 메모만 남아있을 이유가 없다.
        self._purge_local(str(eid))
        self.set_status(f"거래 {eid} 삭제 완료. 재로드 중…")
        self.refresh_entries()

    def _account_type(self, account_id: str) -> str:
        """SessionState 의 flat 에서 account_id 의 type 키 (assets 등)."""
        session = self.app.session  # type: ignore[attr-defined]
        for a in session.accounts_flat:
            if a.get("account_id") == account_id:
                return a.get("type") or ""
        return ""

    # ---- local sqlite (memo + 해시태그) helpers ------------------------

    def _fetch_local_tags(self, entry_id: str) -> list[str]:
        """edit dialog 진입 직전, 로컬 db 에서 entry_id 의 해시태그 prefill.

        실패는 fatal 이 아니다 — db 가 비어있거나 entry 가 처음 보는 거래면
        그냥 빈 list 반환. 어떤 경우든 dialog 가 열리는 흐름을 막지 않는다.
        """
        if not entry_id:
            return []
        try:
            with tui_data.open_ro() as conn:
                rows = core_db.get_annotations_for(conn, [str(entry_id)])
        except Exception:  # pragma: no cover — db 미존재 등
            log.exception("fetch_local_tags failed")
            return []
        info = rows.get(str(entry_id)) or {}
        return list(info.get("hashtags") or [])

    def _fetch_all_tags_db(self) -> dict[str, int]:
        """전체 해시태그 사전 — `{tag: count}`. TagsPickerScreen 의 *추천*
        + *자주 쓰는 태그* 섹션 출처. db 가 비어있으면 빈 사전 (모달은
        그래도 정상 동작 — 새 태그 만들기만 가능).
        """
        try:
            with tui_data.open_ro() as conn:
                return core_db.list_hashtags(conn)
        except Exception:  # pragma: no cover
            log.exception("fetch_all_tags_db failed")
            return {}

    def _persist_local(
        self,
        *,
        entry_id: str,
        section_id: str,
        memo: str,
        tags: list[str],
    ) -> None:
        """후잉 mutation 성공 후 로컬 sqlite 에 memo + 해시태그 동기화.

        memo 는 후잉과 동일값을 그대로 mirror (검색·통계용 로컬 인덱스).
        tags 는 로컬 전용 (후잉에는 보내지 않는다). 둘 다 비었을 때도
        annotation row 는 만들어 둬 (set_hashtags 가 FK 위해 강제 생성).
        """
        if not entry_id:
            return
        try:
            with tui_data.open_rw() as conn:
                core_db.upsert_annotation(
                    conn,
                    entry_id=str(entry_id),
                    section_id=section_id or None,
                    note=memo or None,
                )
                core_db.set_hashtags(conn, str(entry_id), list(tags or []))
        except Exception:  # pragma: no cover
            log.exception("persist_local annotation/hashtags failed")

    def _purge_local(self, entry_id: str) -> None:
        """삭제된 거래의 annotation / 해시태그 정리. CASCADE 가 처리하므로
        upsert 의 역으로 `remove_annotation` 만 호출."""
        if not entry_id:
            return
        try:
            with tui_data.open_rw() as conn:
                core_db.remove_annotation(conn, str(entry_id))
        except Exception:  # pragma: no cover
            log.exception("purge_local annotation failed")

    @staticmethod
    def _extract_entry_id(response: Any) -> str | None:
        """후잉 create_entry 응답에서 새 entry_id 회수. 가능한 모양들:
            - {"entry_id": "..."}
            - {"entries": [{"entry_id": "..."}]}
            - {"results": [{"entry_id": "..."}]}
        못 찾으면 None — 이 경우 로컬 persist 는 건너뛴다 (수정 시점엔
        draft.entry_id 가 이미 있어 본 함수를 안 탄다).
        """
        if not isinstance(response, dict):
            return None
        eid = response.get("entry_id")
        if eid:
            return str(eid)
        for key in ("entries", "results", "data"):
            seq = response.get(key)
            if isinstance(seq, list) and seq and isinstance(seq[0], dict):
                eid = seq[0].get("entry_id")
                if eid:
                    return str(eid)
        return None

    # ---- worker --------------------------------------------------------

    @work(exclusive=True, group="entries", name="refresh_entries")
    async def refresh_entries(self) -> None:
        """sections / accounts / entries 를 chain 으로 부팅 / 재로드.

        section_id 가 비어있으면 sections-list → 자동 활성화.
        accounts_flat 이 비어있으면 accounts-list → SessionState 인덱스
        빌드. 마지막에 entries-list → DataTable 갱신.
        """
        session = self.app.session  # type: ignore[attr-defined]

        # 1. 섹션 미선택 시 sections-list + 자동 활성화 (HomeScreen 자리에서
        #    하던 일이 본 화면으로 흡수된 결과 — CL #51023).
        if not session.section_id:
            try:
                sections = await self._client.list_sections()
            except ToolError as e:
                self.set_status(f"섹션 로드 실패 [{e.kind}] {e.message}", error=True)
                return
            except Exception as e:  # pragma: no cover
                log.exception("entries bootstrap: list_sections failed")
                self.set_status(f"섹션 로드 실패 (INTERNAL): {e}", error=True)
                return
            if not sections:
                self.set_status(
                    "후잉 계정에 섹션이 없습니다 — whooing.com 에서 먼저 생성하세요.",
                    error=True,
                )
                return
            # 자동 활성화 우선순위 (CL #51031+):
            #   ① 저장된 last_section_id (사용자가 한 번이라도 's' 로 직접
            #      선택했으면 그 선택을 다음 부팅에 복원).
            #   ② "Default" 섹션 — title="Default" 또는 응답의 is_default=true.
            #      후잉이 새 계정에 default 로 만드는 섹션의 표준 이름.
            #   ③ WHOOING_SECTION_ID 환경변수 — legacy fallback.
            #   ④ 첫 섹션 — 그것마저 매칭 안 되면.
            chosen = None

            saved_sid = load_last_section_id()
            if saved_sid:
                chosen = next(
                    (s for s in sections
                     if str(s.get("section_id") or s.get("id")) == saved_sid),
                    None,
                )

            if chosen is None:
                chosen = next(
                    (s for s in sections
                     if s.get("is_default") is True or s.get("title") == "Default"),
                    None,
                )

            if chosen is None:
                env_id = default_section_id_from_env()
                if env_id:
                    chosen = next(
                        (s for s in sections
                         if str(s.get("section_id") or s.get("id")) == env_id),
                        None,
                    )

            if chosen is None:
                chosen = sections[0]

            sid = str(chosen.get("section_id") or chosen.get("id"))
            session.set_section(sid, chosen.get("title"))
            # 자동 활성화도 저장 — 다음 부팅에 같은 결정이 빠르게 적용된다.
            save_last_section_id(sid)

        # 2. accounts 캐시 미로드 시 fetch.
        if not session.accounts_flat:
            try:
                raw = await self._client.list_accounts(session.section_id)
            except ToolError as e:
                self.set_status(f"계정과목 로드 실패 [{e.kind}] {e.message}", error=True)
                return
            except Exception as e:  # pragma: no cover
                log.exception("entries bootstrap: list_accounts failed")
                self.set_status(f"계정과목 로드 실패 (INTERNAL): {e}", error=True)
                return
            flat = WhooingClient.flatten_accounts(raw)
            session.set_accounts(raw, flat)

        # 3. entries-list (기존 로직).
        section_id = session.section_id
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

        self._entries = entries_sorted
        self._all_entries = list(entries_sorted)  # 필터 해제 시 복원용 원본
        self.last_entry_count = len(entries_sorted)
        self._render_table(entries_sorted)
        self._update_window_status(start_date, end_date, entries_sorted)

    # ---- render --------------------------------------------------------

    def _format_cell(self, entry: dict[str, Any], col_index: int) -> Any:
        """entry 와 column index 로부터 cell 의 표시 값을 만든다.

        `_render_table` 의 row 추가 + `_update_active_cell_marker` 의 cell
        복원 양쪽에서 같은 형식으로 보이도록 단일 helper. money 만 Rich
        `Text` 로 (오른쪽 정렬, CL #51087+) — 나머지는 `str`.
        """
        session = self.app.session  # type: ignore[attr-defined]
        col = self._COLUMN_NAMES[col_index]
        if col == "date":
            return _fmt_date(entry.get("entry_date"))
        if col == "money":
            return Text(_fmt_money(entry.get("money")), justify="right")
        if col == "left":
            l_id = entry.get("l_account_id") or ""
            return session.title_of(l_id) if l_id else ""
        if col == "right":
            r_id = entry.get("r_account_id") or ""
            return session.title_of(r_id) if r_id else ""
        if col == "item":
            return entry.get("item") or ""
        if col == "memo":
            return entry.get("memo") or ""
        return ""

    def _render_table(
        self,
        entries: list[dict[str, Any]],
        *,
        target_cursor: int | None = None,
    ) -> None:
        """DataTable 을 (선택적으로) sentinel row 1개 + entries N개로 렌더.

        CL #51074+: sentinel 은 `_show_sentinel=True` 일 때만 row 0 으로
        등장. 평소엔 숨겨져 있고, 사용자가 거래 목록 맨 위 row 에서 ↑ 한
        번 더 누르면 토글된다 (`action_row_up`). 빈 entries 일 때는
        진입점 보장을 위해 강제 표시.

        `target_cursor` 가 명시되면 render 후 그 row 로 cursor 이동.
        명시 안 되면 prev_cursor 보존 또는 default (첫 실거래 / sentinel).
        """
        # 빈 entries 면 sentinel 강제 표시 (사용자 진입점 보장).
        if not entries:
            self._show_sentinel = True

        table = self.query_one("#entries-table", DataTable)
        prev_cursor = table.cursor_row
        table.clear()

        if self._show_sentinel:
            # CL #51087+: 라벨을 시각상 가운데 가까운 컬럼 (index 3 = right)
            # 에 두고 Rich Text 의 `justify="center"` 로 그 셀 안에서 가운데
            # 정렬. 다른 컬럼은 빈 셀 — 사용자에게 "전체 행 너비 가운데에
            # + 새 거래 추가 메뉴" 로 보이도록.
            mid = len(self._COLUMN_NAMES) // 2
            cells: list[Any] = [""] * len(self._COLUMN_NAMES)
            cells[mid] = Text(
                self._NEW_ENTRY_SENTINEL_LABEL, justify="center",
            )
            table.add_row(*cells)

        for e in entries:
            cells = [self._format_cell(e, i) for i in range(len(self._COLUMN_NAMES))]
            table.add_row(*cells)

        # cursor 위치 결정:
        # - target_cursor 명시 → 그대로
        # - 그 외: prev_cursor 가 valid entry row 면 보존, 아니면 첫
        #   entry row, entries 비면 sentinel.
        if target_cursor is not None:
            target_row = target_cursor
        else:
            first_entry_row = 1 if self._show_sentinel else 0
            last_entry_row = first_entry_row + len(entries) - 1
            if (
                entries
                and prev_cursor is not None
                and first_entry_row <= prev_cursor <= last_entry_row
            ):
                target_row = prev_cursor
            elif entries:
                target_row = first_entry_row
            else:
                target_row = 0  # sentinel only
        try:
            table.move_cursor(row=target_row, animate=False)
        except Exception:  # pragma: no cover — coord stale
            pass

        # render 후 marker 재적용 (sentinel 아닌 row 에서만).
        self._marked_cell = None
        self._update_active_cell_marker()

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

        section_id = self.app.session.section_id  # type: ignore[attr-defined]
        section_title = self.app.session.section_title  # type: ignore[attr-defined]
        sec_label = (
            f"{section_title} ({section_id})" if section_title else str(section_id)
        )

        if n == 0:
            # 빈 결과 — 사용자가 *왜* 비어있는지 한눈에 알 수 있도록 다음
            # 액션을 status bar 에 명시. warn 클래스로 시각 구분.
            msg = (
                f"거래내역 없음 — section={sec_label}, 최근 {self._window_days}일 "
                f"({start_date}~{end_date}). "
                f"다른 섹션 [s] / 윈도우 확장 [+] / 새 거래 [n]"
            )
            self.set_status(msg, warn=True)
            return

        msg = (
            f"{n}건 표시 ({start_date} ~ {end_date}, 최근 {self._window_days}일, "
            f"section={sec_label})"
        )
        if self.last_cap_warning:
            # status 메시지의 날짜도 표 컬럼과 동일한 YYYY-MM-DD 형식으로.
            cap_list = ", ".join(_fmt_date(d) for d in cap_dates[:3]) + (
                " …" if len(cap_dates) > 3 else ""
            )
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
