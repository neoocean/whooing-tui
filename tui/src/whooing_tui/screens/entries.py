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
  f   AttachmentBrowserScreen — 선택된 거래의 첨부파일 list / 추가 / 삭제 /
      열기. 파일은 `<project_root>/attachment/YYYY/YYYY-MM-DD/<filename>`
      에 sha256 dedup 저장, 거래 ↔ 파일 1:N 관계는 sqlite `entry_attachments`
      테이블 (CL #51123+).

키 바인딩 (Footer 가 표시):
  q / escape       앱 종료 (initial screen 이므로 pop 이 아닌 exit).
  r                재로드 (현재 윈도우, 캐시 강제 invalidate).
  + / -            윈도우 ±7일.
  s / a            섹션 picker / 계정과목 화면 push.
  n / Enter / d    거래 추가 / 수정 / 삭제 (EntryEditDialog / Confirm).
  f                선택 거래의 첨부파일 화면 (AttachmentBrowserScreen).
  ?                화면 도움말 (HelpModal).

DataTable 컬럼:
  date  money  left  right  item  memo

money 는 천단위 콤마. left/right 는 account_id 를 SessionState 의 양방향
인덱스로 즉시 title 로 변환 — 사용자에게는 코드 대신 이름이 보인다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from whooing_core import db as core_db

from whooing_tui import constants
from whooing_tui import data as tui_data
from whooing_tui.client import WhooingClient
from whooing_tui.config import load_config
from whooing_tui.repository import EntryRepository
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
from whooing_tui.widgets import (
    MenuBar,
    MenuBarMixin,
    MenuItem,
    MenuPopup,
    MenuSpec,
    menubar_bindings,
)

log = logging.getLogger(__name__)


def _yesterday_of(yyyymmdd: str | None) -> str | None:
    """YYYYMMDD 의 전날 — 빈 / 잘못된 입력은 그대로 반환.

    CL #52758+: 필터 확장 worker 의 윈도우 경계 조정 (현재 윈도우 oldest -1).
    """
    if not yyyymmdd or len(yyyymmdd) < 8 or not yyyymmdd[:8].isdigit():
        return yyyymmdd
    try:
        d = datetime.strptime(yyyymmdd[:8], "%Y%m%d") - timedelta(days=1)
        return d.strftime("%Y%m%d")
    except ValueError:  # pragma: no cover
        return yyyymmdd


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


class EntriesScreen(MenuBarMixin, Screen):
    """활성 섹션의 거래내역 화면. F10 메뉴바 (MenuBarMixin)."""

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
    /* CL #51096+: cursor 가 sentinel ([+ 새 거래 추가]) 위에 있을 때만
       적용되는 클래스. 일반 거래 row 의 파란 cursor 와 색을 다르게 해
       사용자가 "이 row 는 다른 기능 (입력 추가) 임" 을 즉시 인지하도록.
       focused / blurred 양쪽 다 같은 노란/검정 톤으로 통일 — 의도적으로
       눈에 띄게.  */
    #entries-table.sentinel-active > .datatable--cursor {
        background: $warning;
        color: black;
        text-style: bold;
    }
    #entries-table.sentinel-active:focus > .datatable--cursor {
        background: $warning;
        color: black;
        text-style: bold;
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
        # CL #51116+: 't' (또는 ㅌ) 단축키 — 보고서/통계 드롭다운 메뉴 push.
        *bind_ko("t", "open_reports", "Reports", show=True, priority=True),
        *bind_ko("n", "new_entry", "New", show=True, priority=True),
        # Enter 의 의미는 _column_active + _active_col 에 따라 분기.
        Binding("enter", "context_enter", "Enter", show=True, priority=True),
        *bind_ko("e", "edit_entry", "Edit", show=True, priority=True),
        *bind_ko("d", "delete_entry", "Delete", show=True, priority=True),
        # CL #51123+: 'f' (또는 ㄹ) — 선택 거래의 첨부파일 browser. sentinel
        # row 또는 entry_id 가 없으면 status 안내 후 noop.
        *bind_ko("f", "open_attachments", "Files", show=True, priority=True),
        # CL #52763+: 'm' / 'ㅡ' — 선택 거래의 context menu (수정/삭제/첨부).
        # 사용자 요청: m 으로 메뉴 열어서 삭제 가능하게.
        *bind_ko("m", "show_context_menu", "Menu", show=True, priority=True),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("c", "clear_filter", "Clear", show=True, priority=True),
        Binding("left", "prev_column", "←", show=False, priority=True),
        Binding("right", "next_column", "→", show=False, priority=True),
        # ↑/↓ 는 default DataTable 의 cursor 이동을 가로채서 sentinel
        # 토글까지 처리한다 (CL #51074+).
        Binding("up", "row_up", "↑", show=False, priority=True),
        Binding("down", "row_down", "↓", show=False, priority=True),
        # CL #52771+: Home/End/PgUp/PgDn — 큰 entries 목록에서 빠른 이동.
        # priority=True 인 up/down 이 default DataTable navigation 을 가려
        # 같은 우선순위 keys 도 명시해야 동작.
        Binding("home", "row_home", "Home", show=False, priority=True),
        Binding("end", "row_end", "End", show=False, priority=True),
        Binding("pageup", "row_pageup", "PgUp", show=False, priority=True),
        Binding("pagedown", "row_pagedown", "PgDn", show=False, priority=True),
        # CL #52773+: Shift + navigation — 범위 multi-select.
        # anchor (이전 cursor) 부터 새 cursor 까지의 entries 를 selection set 에.
        Binding("shift+up", "row_select_up", "Shift+↑", show=False, priority=True),
        Binding("shift+down", "row_select_down", "Shift+↓", show=False, priority=True),
        Binding("shift+home", "row_select_home", "Shift+Home", show=False, priority=True),
        Binding("shift+end", "row_select_end", "Shift+End", show=False, priority=True),
        Binding("shift+pageup", "row_select_pageup", "Shift+PgUp", show=False, priority=True),
        Binding("shift+pagedown", "row_select_pagedown", "Shift+PgDn", show=False, priority=True),
        Binding("question_mark", "help", "Help", show=True, priority=True, key_display="?"),
        Binding("plus", "extend_window", "+7d", show=True),
        Binding("minus", "shrink_window", "-7d", show=True),
        Binding("equals_sign", "extend_window", "", show=False),  # '+' 키 (no shift)
        # CL #51145+ (H6) multi-select — space 토글 / # 일괄 태그.
        Binding("space", "toggle_selection", "Sel", show=True, priority=True),
        Binding("number_sign", "batch_tag", "Batch tag", show=True, priority=True),
        # CL #51126+ F10 메뉴 — CL #51131+ 부터 menubar_bindings() 로 추출.
        *menubar_bindings(),
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
    # CL #51102+: item 셀 안의 *태그* 가 선택된 상태 — 일반 컬럼 marker 와
    # 다른 색 (cyan) 으로 시각 구분 (사용자 지시: "커서 색상이 바뀌며").
    _TAG_MARKER_STYLE = "black on cyan"

    # CL #51072 부터: DataTable 의 row 0 은 "새 거래 추가" sentinel.
    # cursor 가 row 0 일 때 enter = action_new_entry. 실 거래는 row 1+ 부터.
    # sentinel 의 첫 column 텍스트 — 사용자가 cursor 를 올렸을 때 무엇을
    # 하는 자리인지 즉시 알 수 있도록. 다른 column 은 빈 cell.
    _NEW_ENTRY_SENTINEL_LABEL = "[+ 새 거래 추가]"

    # 한 번에 fetch 할 거래는 후잉 server-side hard cap 100건. 단일 일자에
    # 100건 초과는 _list_entries_chunked 가 더 분할할 수 없으므로 footer
    # 에 경고 띄움 (DESIGN §4.3 + MEMORY §7). CL #52834+ 부터 `constants`
    # 모듈로 일원화 — 본 클래스 속성은 alias.
    _SERVER_PAGE_CAP = constants.WHOOING_SERVER_PAGE_CAP

    # CL #51126+: F10 풀다운 메뉴바. 사용자 요청 — 기능이 늘어나면서 모든
    # 진입점을 한 곳에. 메뉴는 모듈 함수 _build_menus 가 list 로 반환 —
    # 화면이 메뉴 항목 선택 (action_id) 을 받아 기존 action_* 또는 신규
    # action_import_card_statement 등으로 dispatch.
    @staticmethod
    def _build_menus() -> tuple[MenuSpec, ...]:
        return (
            MenuSpec(
                name="파일",
                items=(
                    MenuItem("재로드 (r)", "refresh"),
                    MenuItem("종료 (q)", "back"),
                ),
            ),
            MenuSpec(
                name="입력",
                items=(
                    MenuItem("새 거래 (n)", "new_entry"),
                    MenuItem("카드 명세서 import…", "import_card_statement"),
                    MenuItem("PDF 영수증/인보이스 첨부…", "attach_receipt"),
                    MenuItem("매월입력 거래 관리…", "open_monthly"),
                ),
            ),
            MenuSpec(
                name="화면",
                items=(
                    MenuItem("섹션 (s)", "open_sections"),
                    MenuItem("계정과목 (a)", "open_accounts"),
                    MenuItem("보고서 / 통계 (t)", "open_reports"),
                    MenuItem("선택 거래 첨부 (f)", "open_attachments"),
                    MenuItem("해시태그 관리…", "open_tag_management"),
                    MenuItem("예산 편집…", "open_budget_edit"),
                    MenuItem("목표 편집…", "open_goal_edit"),
                ),
            ),
            MenuSpec(
                name="도움말",
                items=(
                    MenuItem("키보드 단축키 (?)", "help"),
                ),
            ),
        )

    # CL #51120: 좁은 터미널 단일 임계값 60. CL #51125 사용자 요청으로
    # 4단계 점진 축소 정책으로 확장:
    #   level 0 (>=80): 정상 (6 컬럼).
    #   level 1 (<80) : memo 숨김.
    #   level 2 (<60) : + left/right 헤더 'L'/'R' 약어, 셀은 한글 2글자.
    #   level 3 (<45) : + right 컬럼 숨김.
    #   level 4 (<35) : + left 컬럼도 숨김.
    # _NARROW_THRESHOLD 는 후방 호환 — `_compact` boolean property 와 함께
    # 기존 호출자/테스트가 깨지지 않도록 유지 (level >= 2 가 종래 의미의
    # 컴팩트). 임계값 자체는 _COMPACT_THRESHOLDS 가 source of truth.
    # CL #52834+ 부터 `constants.COMPACT_THRESHOLDS` 로 일원화 — 클래스
    # 속성은 alias (legacy 호출자 보호).
    _COMPACT_THRESHOLDS: tuple[int, ...] = constants.COMPACT_THRESHOLDS
    _NARROW_THRESHOLD: int = constants.COMPACT_THRESHOLDS[1]  # 60

    # CL #51125+: left/right 셀 약어 시 사전 제거할 괄호류. 한글 인용부호
    # (「」 『』) 까지 — 사용자 답변에서 명시. isalnum 일반화는 보수성을
    # 위해 안 함 (whitespace / 다른 punct 보존). 셀에서 한 번에 strip.
    _ABBREV_BRACKETS: str = "()[]{}「」『』"
    # CL #51127+: 회사 식별자 prefix — strip 후 본 이름만 남기고 줄임.
    # "(주)스타벅스" → "(주)" strip 후 "스타벅스" → 한국식 4글자 약어 "스벅".
    # 선두 매칭만 (어디 가운데에 있어도 strip X). tuple 순서가 곧 시도 순서 —
    # 더 긴 prefix 부터 두어 부분 매칭 회피 (예: "주식회사" 가 "(주)" 보다 먼저).
    _ABBREV_COMPANY_PREFIXES: tuple[str, ...] = (
        "주식회사 ", "주식회사",
        "유한회사 ", "유한회사",
        "재단법인 ", "재단법인",
        "사단법인 ", "사단법인",
        "(주)", "(유)", "(재)", "(사)",
    )
    # CL #51130+: 회사명 끝의 의미상 *부가어* — strip 후 한국식 약어 적용.
    # "스타벅스코리아" → "코리아" strip → "스타벅스" (4자) → 1+3 = "스벅".
    # 사용자 답변에서 미리 합의된 list — 너무 공격적이면 일반 한글 단어
    # ("강남" / "서울" 등) 도 잘릴 수 있어 *대문자 회사 명사* 만 보수적으로.
    # 더 긴 suffix 부터 시도 ("인터내셔널" > "내셔널" 같은 부분 매칭 회피).
    _ABBREV_COMPANY_SUFFIXES: tuple[str, ...] = (
        "엔터프라이즈", "인터내셔널",
        "코퍼레이션", "이노베이션",
        "홀딩스", "그룹", "글로벌", "코리아",
    )
    # 한글 음절 범위 (Hangul Syllables block, U+AC00~U+D7A3) — 첫 글자가
    # 한글이면 한국식 줄임말 규칙 적용, 그 외 (영문/숫자/혼합) 면 단순 [:2].
    # CL #52834+ 부터 `constants` 모듈로 일원화 — 클래스 속성은 alias.
    _HANGUL_FIRST = constants.HANGUL_SYLLABLE_FIRST
    _HANGUL_LAST = constants.HANGUL_SYLLABLE_LAST
    _ABBREV_CHARS: int = constants.ABBREV_KOREAN_CHARS

    def __init__(self, client: WhooingClient) -> None:
        super().__init__()
        self._client = client
        # CL #52834+: 로컬 sqlite annotation/태그/첨부 카운트 호출을 한
        # repo 로 위임. 본 화면의 _fetch_*/_persist_local/_purge_local 은
        # 후방 호환 wrapper.
        self._repo = EntryRepository()
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
        # CL #52758+ 필터 결과 점진적 확장. 현재 윈도우 (_all_entries) 밖
        # 에서 발견된 매칭 — sqlite 캐시 또는 background fetch worker 가 채움.
        # 필터 해제 / 새 필터 시작 시 비움.
        self._filter_extra: list[dict[str, Any]] = []
        # 필터 worker 의 epoch — 사용자가 필터를 바꾸거나 해제하면 +1 해서
        # 진행 중인 worker 가 자기 결과를 무시하도록 (race 방지).
        self._filter_epoch: int = 0
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
        # CL #51102 부터: entry_id → 해시태그 list. refresh_entries 가
        # core_db.get_annotations_for 로 batch fetch 해서 채운다. 비어있으면
        # item 컬럼에 태그 인라인 표시 X / 태그 단위 네비 X.
        self._entry_tags: dict[str, list[str]] = {}
        # CL #51134+ (A6): entry_id → 첨부 개수. 0 이면 indicator 미표시.
        # refresh_entries 가 list_attachments_for 한 번으로 batch fetch.
        self._entry_attachment_counts: dict[str, int] = {}
        # CL #51145+ (H6): 일괄 작업용 multi-select. space 토글, '#' = 일괄 태그.
        # entry_id 의 set — refresh 시 reset (사라진 entry 의 stale 방지).
        self._selected_entry_ids: set[str] = set()
        # CL #52773+: Shift+화살표 / Shift+click 의 anchor row — 마지막 단일
        # selection 또는 cursor 가 anchor. None 이면 현재 cursor 가 anchor 로
        # set 되며 시작. selection 해제 (`action_clear_filter` 등) 시 None.
        self._selection_anchor: int | None = None
        # CL #51151+ (H11): tag → color (Rich/Textual 색명).
        self._tag_colors: dict[str, str] = {}
        # 태그 단위 column 네비 — `_active_col == _COL_INDEX["item"]` +
        # `_column_active=True` 인 상태에서 → 한 번 더 누르면 0 부터 그 row
        # 의 태그 개수 - 1 까지 sliding. None = 태그 모드 아님 (item 셀 자체
        # marker). row 가 ↑/↓ 로 바뀌면 None 으로 reset (각 row 의 태그
        # 개수가 달라 index 보존이 의미 없음).
        self._tag_index: int | None = None
        # CL #51120 / CL #51125+: 좁은 터미널 컴팩트 모드. 종전엔 boolean
        # 이었으나 사용자 요청으로 4단계로 확장 (`_COMPACT_THRESHOLDS` 참고).
        # 0 = 정상, 1 = memo 숨김, 2 = + L/R 약어, 3 = + right 숨김,
        # 4 = + left 숨김. _COLUMN_NAMES 의 인덱스 정의는 유지 (네비/marker
        # 코드 안 깨지게).
        self._compact_level: int = 0

    # ---- compose -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        # CL #51126+: 메뉴바는 Header 와 본문 사이 — 항상 visible.
        yield MenuBar(self._build_menus(), id="entries-menubar")
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
        # CL #51120 / CL #51125+: 초기 size 기준으로 level 결정 + 컬럼 적용.
        self._compact_level = self._compute_compact_level()
        self._apply_column_widths_for_size()
        self.set_status("거래내역 로드 중…")
        self.refresh_entries()
        table.focus()

    # ---- 좁은 터미널 (iPhone Blink 등) 적응 (CL #51120 / CL #51125+) ------

    @property
    def _compact(self) -> bool:
        """후방 호환 — `_compact_level >= 2` 가 종래 의미의 "컴팩트 모드"
        (left/right 가 약어 또는 hidden 으로 시각이 줄어든 상태). level 1
        (memo 만 숨김) 은 여전히 left/right 가 정상 표시라 종래 호출자
        (네비 hidden-skip 등) 입장에서 "정상 모드".
        """
        return self._compact_level >= 2

    def _is_narrow_size(self) -> bool:
        """후방 호환 — 종래 단일 임계값 기준 boolean. 신규 코드는
        `_compute_compact_level()` 직접 사용."""
        return self._compute_compact_level() >= 2

    def _compute_compact_level(self) -> int:
        """현재 터미널 너비로 컴팩트 단계 (0~4) 계산. CL #51125+ /
        #51158+ (review C4): pure logic 은 entries_compact.compute_compact_level.
        본 method 는 self.app.size.width 회수 + side-effect 처리.
        """
        from whooing_tui.screens.entries_compact import compute_compact_level
        try:
            w = self.app.size.width
        except Exception:  # pragma: no cover — size 미정의 환경
            return 0
        return compute_compact_level(w)

    @classmethod
    def _is_hangul(cls, ch: str) -> bool:
        """단일 글자가 한글 음절 (Hangul Syllables block) 인지.

        CL #51158+ (review C4): pure helper 가 entries_compact 로 이동.
        본 method 는 후방 호환 wrapper.
        """
        from whooing_tui.screens.entries_compact import is_hangul
        return is_hangul(ch)

    @classmethod
    def _abbreviate_account_name(cls, name: str) -> str:
        """계정명을 좁은 컬럼용으로 약어. CL #51125 (단순 [:2]) → CL #51127
        한국식 줄임말 규칙으로 강화 (사용자 요청).

        절차:
          1. 회사 식별자 prefix strip (선두에 있을 때만):
             "(주)", "(유)", "주식회사", "유한회사" 등 — `_ABBREV_COMPANY_
             PREFIXES` 의 첫 매칭. 한 번만 strip (중복 매칭 X).
          2. 잔여 괄호류 (`(){}[]「」『』`) 모두 제거 + strip 공백.
          3. 길이 + 첫 글자가 한글인지로 분기:
             - 빈 문자열 → "".
             - 첫 글자 한글 + 길이 4 → 1번째 + 3번째 글자 (한국식: 스타
               벅스→스벅, 맥도날드→맥날, 삼성전자→삼전).
             - 첫 글자 한글 + 길이 3 → 앞 2글자 (교통비→교통).
             - 첫 글자 한글 + 길이 2 이하 → 그대로 (식비→식비).
             - 첫 글자 한글 + 길이 5 이상 → 앞 2글자 보수 fallback
               (현대자동차→현대).
             - 첫 글자 비-한글 (영문/숫자/혼합) → 앞 2글자 (Starbucks→St).
               한국식 1+3 규칙은 의미 단위 분할 가정이라 영문엔 부적용.

        예:
          "(주)스타벅스"      → "스벅"
          "스타벅스"          → "스벅"
          "맥도날드"          → "맥날"
          "삼성전자"          → "삼전"
          "주식회사 카카오"   → "카카"
          "교통비"            → "교통"
          "식비"              → "식비"
          "현대자동차"        → "현대"
          "[자산]현금"        → "자산"   # prefix 아닌 일반 괄호.
          "Starbucks"         → "St"
          "T맵"               → "T맵"   # 첫 글자 영문 → [:2] = "T맵".
          ""                  → ""
        """
        # CL #51158+ (review C4): pure helper 가 entries_compact 로 이동.
        # 본 method 는 후방 호환 wrapper.
        from whooing_tui.screens.entries_compact import abbreviate_account_name
        return abbreviate_account_name(name)

    # 단계별 컬럼 표시 정책 — _apply_column_widths_for_size + _column_is_visible
    # 둘 다 본 표를 참고. 인덱스 (left=2, right=3, memo=5) 는 _COLUMN_NAMES
    # 와 1:1 매핑. width 값 의미: >0 = 고정 폭, 0 = auto_width 면 콘텐츠 만큼,
    # auto_width=False 면 강제 hidden. 본 표는 설정 결과를 의미상 명시.
    #
    # level 0 (>=80)  | left=12, right=auto,  memo=auto, headers normal
    # level 1 (<80)   | left=12, right=auto,  memo=hidden
    # level 2 (<60)   | left=4,  right=4,     memo=hidden, headers L/R, 셀 약어
    # level 3 (<45)   | left=4,  right=hidden, memo=hidden, header L,    셀 약어
    # level 4 (<35)   | left=hidden, right=hidden, memo=hidden
    _ABBREV_COL_WIDTH: int = 4  # 한글 2글자 = display 폭 4 (CJK wide).

    def _apply_column_widths_for_size(self) -> None:
        """현재 `_compact_level` 에 맞춰 컬럼 width / label / auto_width 를
        runtime 변경. 호출 후 `_render_table(self._entries)` 가 셀 내용도
        재포맷해야 약어/숨김이 시각상 반영된다 — `on_resize` 가 책임짐.

        Textual `Column.width` 정책:
          - width=0, auto_width=True  → 콘텐츠 폭으로 자람 (visible).
          - width>0                   → 고정 폭 (visible).
          - width=0, auto_width=False → strict hidden (콘텐츠 무시).
        """
        try:
            table = self.query_one("#entries-table", DataTable)
        except Exception:  # pragma: no cover
            return
        cols = list(table.columns.values())
        if len(cols) < len(self._COLUMN_NAMES):
            return
        # 매 호출마다 일관된 baseline 으로 reset — 이전 level 의 잔재 제거.
        # 각 컬럼은 (width, auto_width, label) 셋을 명시.
        from rich.text import Text  # 지역 import — 모듈 import 비용 회피.

        lvl = self._compact_level
        # date(0) / money(1) / item(4) 는 모든 level 에서 visible — 변경 X.
        # 단 헤더 label 은 처음 add_column 으로 설정된 그대로.

        # --- left (col 2) ---
        if lvl >= 4:
            cols[2].width = 0
            cols[2].auto_width = False
            cols[2].label = Text("left")
        elif lvl >= 2:
            cols[2].width = self._ABBREV_COL_WIDTH
            cols[2].auto_width = False
            cols[2].label = Text("L")
        else:
            cols[2].width = 12
            cols[2].auto_width = False
            cols[2].label = Text("left")

        # --- right (col 3) ---
        if lvl >= 3:
            cols[3].width = 0
            cols[3].auto_width = False
            cols[3].label = Text("right")
        elif lvl >= 2:
            cols[3].width = self._ABBREV_COL_WIDTH
            cols[3].auto_width = False
            cols[3].label = Text("R")
        else:
            cols[3].width = 0
            cols[3].auto_width = True
            cols[3].label = Text("right")

        # --- memo (col 5) ---
        if lvl >= 1:
            cols[5].width = 0
            cols[5].auto_width = False
            cols[5].label = Text("memo")
        else:
            cols[5].width = 0
            cols[5].auto_width = True
            cols[5].label = Text("memo")

        # 강제 refresh — DataTable 이 layout 을 다시 계산.
        try:
            table.refresh(layout=True)
        except Exception:  # pragma: no cover
            pass

    def on_resize(self, event) -> None:
        """터미널 resize → 컴팩트 단계 변경 + 컬럼 width 재적용 + 재렌더.

        CL #51125+: 단계 전환 시 left/right 셀 내용도 약어/원본을 다시 채워
        넣어야 하므로 `_render_table(self._entries)` 호출.
        """
        new_lvl = self._compute_compact_level()
        if new_lvl == self._compact_level:
            return
        prev_lvl = self._compact_level
        self._compact_level = new_lvl
        self._apply_column_widths_for_size()
        # 셀 내용을 단계에 맞게 다시 — 약어 ↔ 원본 전환 시 필수.
        if self._entries or self._show_sentinel:
            try:
                self._render_table(self._entries)
            except Exception:  # pragma: no cover
                log.debug("re-render after resize failed", exc_info=True)
        # 사용자에게 시각상 알림 (status bar). 단계별로 의미가 다르므로 분기.
        msgs = {
            0: "정상 모드 — 모든 컬럼 표시",
            1: "컴팩트(1) — memo 숨김",
            2: "컴팩트(2) — memo 숨김 + L/R 약어 (한글 2글자)",
            3: "컴팩트(3) — memo + right 숨김, L 약어",
            4: "컴팩트(4) — memo + right + left 모두 숨김",
        }
        self.set_status(msgs.get(new_lvl, msgs[0]))

    # ---- actions -------------------------------------------------------

    def action_back(self) -> None:
        """초기 화면이라 pop 대신 앱 종료.

        CL #52819+: 즉시 `app.exit()` 대신 `action_graceful_quit` 로 위임 —
        종료 모달 + 진행 중 commands 표시 + p4 flush 완료 보장. 종전엔 q
        직후 TUI 가 사라지고 CLI 가 멈춘 듯 보였다 (non-daemon p4 thread
        들이 process 를 살아있게 함).
        """
        self.app.action_graceful_quit()

    def action_help(self) -> None:
        """현재 화면의 BINDINGS 를 모달로 보여줌."""
        from whooing_tui.screens.help import HelpModal
        self.app.push_screen(HelpModal("EntriesScreen", list(self.BINDINGS)))

    # ---- CL #51126+ 풀다운 메뉴 (F10) — MenuBarMixin 기반 (CL #51131+) -

    def _menubar_widget_id(self) -> str:
        """본 화면의 MenuBar id — MenuBarMixin 의 query 가 사용."""
        return "entries-menubar"

    def action_attach_receipt(self) -> None:
        """메뉴 → PDF 영수증/인보이스 첨부 wizard.

        파일 경로 모달 → ReceiptAttachScreen push (자동 추출 / 후잉 거래
        매칭 / 사용자 선택으로 첨부 또는 신규 거래 제안).
        """
        self._attach_receipt_wizard()

    @work(exclusive=True, group="wizard", name="attach_receipt")
    async def _attach_receipt_wizard(self) -> None:
        from whooing_tui.screens.receipt_attach import ReceiptAttachScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status(
                "활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True,
            )
            return
        path = await self.app.push_screen_wait(_FilePathModal(
            title="PDF 영수증 / 인보이스 파일 경로",
            placeholder="/Users/me/Downloads/receipt.pdf",
        ))
        if not path:
            self.set_status("영수증 첨부 취소.")
            return
        self.app.push_screen(ReceiptAttachScreen(
            client=self._client, session=session, file_path=path,
        ))

    def action_import_card_statement(self) -> None:
        """메뉴 wizard 진입 — 실제 흐름은 worker 안 (push_screen_wait)."""
        self._import_card_statement_wizard()

    @work(exclusive=True, group="wizard", name="import_card_statement")
    async def _import_card_statement_wizard(self) -> None:
        """카드사 명세서 (HTML / CSV / PDF) → 거래로 import wizard.

        CL #51126+ 사용자 요청: "신용카드회사의 암호화된 명세서 파일을
        입력하면 암호화를 해제해 중복 없이 누락된 항목을 가계부에 입력하는
        기능". 본 wizard 는 다음 3단계:

          1. 파일 경로 입력 modal — 절대 경로.
          2. AccountPickerScreen (side="right") — 카드 계정 선택.
          3. StatementImportScreen push — 자동 detect + extract + dedup +
             Ctrl+Enter 로 입력.

        HTML 보안메일 비밀번호는 `.env` 의 `WHOOING_CARD_HTML_PASSWORD` 에
        있으면 자동 사용 (사용자 답변 — 매번 입력 X). 없으면 import 화면
        안의 PasswordModal 이 fallback 으로 묻는다.

        취소 또는 실패 시 status 안내, 화면은 entries 그대로.
        """
        from whooing_tui.screens.account_picker import AccountPickerScreen
        from whooing_tui.screens.statement_import import StatementImportScreen

        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True)
            return

        # CL #52952+: import 화면에서 Esc → 파일 선택부터 다시. 본 wizard 는
        # 사용자가 "done" 으로 명시 종료할 때까지 반복.
        picked: tuple | None = None
        while True:
            # 1단계 — 파일 경로.
            path = await self.app.push_screen_wait(_FilePathModal(
                title="카드 명세서 파일 경로",
                placeholder="/Users/me/Downloads/statement.html",
            ))
            if not path:
                self.set_status("명세서 import 취소됨.")
                return

            # 2단계 — 카드 계정 선택 (대변). 이전 wizard iteration 에서 picked
            # 했으면 재사용 (사용자가 같은 카드의 다른 명세서를 연달아 import
            # 하는 케이스 — 매번 picker 띄우는 건 거추장스러움).
            if picked is None:
                picked = await self.app.push_screen_wait(
                    AccountPickerScreen(
                        session, side="right",
                        purpose=(
                            "선택한 명세서 안의 거래들을 어느 카드 계정으로 분류할지 "
                            "선택하세요.\n"
                            "(import wizard 2/3 단계 · Esc 로 1단계 (파일 선택) 으로)"
                        ),
                        default_expanded_type="liabilities",
                    ),
                )
                if not picked:
                    # 카드 선택을 취소했으면 wizard 종료 (1단계 재시도는
                    # 사용자가 또 메뉴를 띄워서).
                    self.set_status("카드 계정 미선택 — import 취소.")
                    return
            # AccountPickerScreen 의 dismiss = (account_id, type, title).
            try:
                r_account_id = picked[0]
                card_label = picked[2] if len(picked) > 2 else None
            except Exception:  # pragma: no cover
                self.set_status("카드 계정 형식 오류 — import 취소.", error=True)
                return

            # 3단계 — StatementImportScreen. 결과 값:
            #   "done" → 사용자가 결과 modal 의 OK 를 눌렀음 → wizard 종료.
            #   "back" / None → 사용자가 Esc → 파일 선택부터 다시.
            result = await self.app.push_screen_wait(StatementImportScreen(
                client=self._client,
                section_id=session.section_id,
                r_account_id=r_account_id,
                file_path=path,
                card_label=card_label,
            ))
            if result == "done":
                return
            # "back" / None — loop back to file picker. picked 는 유지.
            self.set_status("파일 선택으로 돌아갑니다…")

    def _dispatch_menu_action(self, action_id: str) -> None:
        """메뉴 항목 선택 → 기존 action_* 메서드 또는 신규 액션으로 위임.

        대부분 항목은 키보드 단축키가 있는 기존 action 과 1:1 매핑 — 이름
        규칙 그대로 `action_<id>` 호출. 예외는 별도 분기.
        """
        # 명시 분기 (메서드명이 다르거나 별도 wizard 가 필요한 경우).
        special = {
            "import_card_statement": self.action_import_card_statement,
        }
        if action_id in special:
            special[action_id]()
            return
        method_name = f"action_{action_id}"
        method = getattr(self, method_name, None)
        if callable(method):
            method()
        else:  # pragma: no cover — 메뉴 정의 ↔ action 누락 시 안전망.
            self.set_status(
                f"메뉴 액션 구현 안 됨: {action_id}", error=True,
            )

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

    def action_open_reports(self) -> None:
        """CL #51116+: 통계 / 보고서 드롭다운 메뉴 (`t` 또는 ㅌ).

        CL #52792+ (사용자 요청): 좌측 메뉴 + 우측 결과를 한 큰 모달로
        통합 (`ReportsScreen`). ↑/↓ 이동 → 자동 fetch + 우측 패널 갱신.
        종전 `ReportsMenuScreen` / `ReportResultScreen` 은 backward compat
        으로 유지.
        """
        from whooing_tui.screens.reports import ReportsScreen

        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True)
            return

        self.app.push_screen(
            ReportsScreen(client=self._client, session=session),
        )

    # ---- CL #51145+ (H6) multi-select + batch tagging ------------------

    def action_toggle_selection(self) -> None:
        """현재 cursor 의 entry 를 selection set 에 토글. sentinel 은 무시.

        CL #52781+: 토글 후 `_render_table` 호출 — 종전엔 갱신 누락이라
        사용자가 space 눌러도 화면이 그대로 보여 "안 됐다" 고 인식.
        cursor 위치는 그대로 유지 (target_cursor 명시).
        """
        target = self._selected_entry()
        if target is None:
            return
        eid = str(target.get("entry_id") or "")
        if not eid:
            return
        if eid in self._selected_entry_ids:
            self._selected_entry_ids.discard(eid)
        else:
            self._selected_entry_ids.add(eid)
        n = len(self._selected_entry_ids)
        # 화면 즉시 갱신 — selection prefix / 색반전 반영.
        try:
            table = self.query_one("#entries-table", DataTable)
            cur_row = table.cursor_row
        except Exception:  # pragma: no cover
            cur_row = None
        self._render_table(self._entries, target_cursor=cur_row)
        self.set_status(
            f"선택 {n}건. space=토글 / m=메뉴 / #=일괄 태그 / Esc=해제"
        )

    def action_batch_tag(self) -> None:
        """선택 entry 들에 tag 일괄 추가/제거 — TagsPickerScreen 재사용."""
        if not self._selected_entry_ids:
            # 선택 0이면 현재 cursor entry 만 (편의).
            target = self._selected_entry()
            if target is None or not target.get("entry_id"):
                self.set_status(
                    "선택된 거래가 없습니다 — space 로 1건 이상 선택.",
                    error=True,
                )
                return
            self._selected_entry_ids.add(str(target["entry_id"]))
        self._batch_tag_worker()

    @work(exclusive=True, group="batch", name="batch_tag")
    async def _batch_tag_worker(self) -> None:
        from whooing_tui.screens.tags_picker import TagsPickerScreen
        eids = sorted(self._selected_entry_ids)
        if not eids:
            return
        # TagsPicker 로 단일 태그 선택. 사용자에게 "어떤 태그를 추가/제거할지".
        existing = self._fetch_all_tags_db()
        tag = await self.app.push_screen_wait(TagsPickerScreen(
            item="(일괄)", memo=f"{len(eids)}건 선택됨",
            existing=existing,
        ))
        if not tag:
            self.set_status("일괄 태그 취소.")
            return
        # 추가/제거 confirm — 단순화: 항상 추가. 제거는 별도 키 (후속).
        session = self.app.session  # type: ignore[attr-defined]
        try:
            with tui_data.open_rw() as conn:
                added = core_db.add_tag_to_entries(
                    conn, eids, tag, section_id=session.section_id,
                )
        except Exception as ex:
            self.set_status(f"일괄 태그 실패: {ex}", error=True)
            return
        from whooing_tui import p4_sync
        p4_sync.submit_db_to_p4(
            tui_data.db_path(),
            f"[whooing-tui] batch tag add #{tag}: {added}/{len(eids)} entries "
            f"(section={session.section_id})",
        )
        self.set_status(
            f"#{tag} → {added}건 추가 (이미 있던 {len(eids) - added}건 skip). "
            f"selection 해제."
        )
        self._selected_entry_ids.clear()
        self.refresh_entries()

    def action_evaluate_duplicates(self) -> None:
        """CL #52815+: 선택 2건 이상의 중복 여부 평가 + dedup 인터페이스.

        사용자 요청 — m 컨텍스트메뉴에 노출, 선택된 거래들에 대해 다양한
        휴리스틱으로 중복을 평가, 중복이면 keep 하나 + 나머지 삭제 인터페이스,
        아니면 "중복 아님" 표시 후 닫기.

        worker context — push_screen_wait 가 worker 필요. 결과가 True
        (dedup 실행) 면 entries 재로드.
        """
        if len(self._selected_entry_ids) < 2:
            self.set_status(
                "중복 평가는 2건 이상 선택해야 합니다 (space 로 선택).",
                error=True,
            )
            return
        self._evaluate_duplicates_worker()

    @work(exclusive=True, group="dupe", name="evaluate_duplicates")
    async def _evaluate_duplicates_worker(self) -> None:
        from whooing_tui.screens.dupe_eval import DuplicateEvalScreen

        session = self.app.session  # type: ignore[attr-defined]
        selected_ids = set(self._selected_entry_ids)
        targets = [
            e for e in self._entries
            if str(e.get("entry_id") or "") in selected_ids
        ]
        if len(targets) < 2:
            self.set_status(
                "선택된 거래를 찾을 수 없습니다 — 재로드 후 다시 시도.",
                error=True,
            )
            return

        async def _delete_many(eids: list[str]) -> tuple[int, list[str]]:
            """후잉 삭제 + 로컬 db 정리 — dedup 화면이 호출."""
            deleted = 0
            failed: list[str] = []
            for eid in eids:
                try:
                    await self._client.delete_entry(
                        section_id=session.section_id, entry_id=eid,
                    )
                    deleted += 1
                    try:
                        self._purge_local(eid)
                    except Exception:  # pragma: no cover — db 없음 등
                        log.exception("purge_local %s failed", eid)
                except ToolError as e:
                    failed.append(f"{eid} [{e.kind}] {e.message}")
                except Exception as e:  # pragma: no cover
                    log.exception("dedup delete %s failed", eid)
                    failed.append(f"{eid} INTERNAL: {e}")
            return deleted, failed

        result = await self.app.push_screen_wait(  # type: ignore[attr-defined]
            DuplicateEvalScreen(
                targets,
                client=self._client,
                session=session,
                delete_callback=_delete_many,
            ),
        )
        if result is True:
            self._selected_entry_ids.clear()
            self.set_status("중복 정리 완료. 재로드 중…")
            self.refresh_entries()
        elif result is False:
            self.set_status("중복 평가 종료 (변경 없음).")
        # None (Esc) — silent.

    def action_open_monthly(self) -> None:
        """CL #51152+: 매월입력 거래 관리 화면."""
        from whooing_tui.screens.monthly_entries import MonthlyEntriesScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status(
                "활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True,
            )
            return
        self.app.push_screen(MonthlyEntriesScreen(self._client, session))

    def action_open_budget_edit(self) -> None:
        """CL #51153+: 예산 입력/편집 화면."""
        from whooing_tui.screens.budget_edit import BudgetEditScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status(
                "활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True,
            )
            return
        self.app.push_screen(BudgetEditScreen(self._client, session))

    def action_open_goal_edit(self) -> None:
        """CL #51154+: 목표 (장기 + 월별 자본) 편집 화면."""
        from whooing_tui.screens.goal_edit import GoalEditScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status(
                "활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True,
            )
            return
        self.app.push_screen(GoalEditScreen(self._client, session))

    def action_open_tag_management(self) -> None:
        """CL #51135+ (H5): 해시태그 일괄 관리 화면 push.

        현재 활성 섹션의 태그만 — cross-section 오염 방지. 섹션 미선택이면 안내.
        """
        from whooing_tui.screens.tag_management import TagManagementScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True)
            return

        def _on_close(_: Any) -> None:
            # 태그 변경 후 entries 의 inline 표시도 fresh 하게.
            self.refresh_entries()

        self.app.push_screen(
            TagManagementScreen(section_id=session.section_id),
            _on_close,
        )

    def action_open_attachments(self) -> None:
        """CL #51123+: 선택 거래의 첨부파일 browser 로 push.

        sentinel row (새 거래 추가 자리) 또는 entry_id 가 비어있는 거래는
        첨부 대상 자체가 없으므로 status 안내 후 noop. 실 거래라면
        AttachmentBrowserScreen 이 a/d/o/r 로 추가/삭제/열기/새로고침을
        제공한다. 파일 본체는 `<project_root>/attachment/YYYY/YYYY-MM-DD/`
        에 sha256 dedup 저장 (`whooing_tui.data.attachments_root()` 결정).
        화면을 닫고 돌아오면 entries 목록은 그대로 — 첨부 변경은 후잉 거래
        자체에 영향을 주지 않으므로 재로드 불필요.
        """
        from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen

        # sentinel row 면 entry 없음 — 안내.
        if self._is_on_sentinel_row():
            self.set_status(
                "새 거래 추가 자리는 첨부 대상이 아닙니다 — 거래를 먼저 만드세요.",
                warn=True,
            )
            return
        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        eid = target.get("entry_id")
        if not eid:
            self.set_status(
                "이 거래에는 entry_id 가 없습니다 — 첨부 불가.", error=True,
            )
            return
        session = self.app.session  # type: ignore[attr-defined]
        self.app.push_screen(
            AttachmentBrowserScreen(
                entry_id=str(eid),
                section_id=session.section_id,
            ),
        )

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
        """필터 해제 — _all_entries 를 그대로 다시 표시 (재로드 X).

        CL #52758+: 진행 중인 _expand_filter_in_past worker 가 있으면 epoch
        bump 으로 결과 폐기.
        """
        if self._active_filter is None:
            self.set_status("활성 필터 없음.")
            return
        self._active_filter = None
        self._filter_epoch += 1
        self._filter_extra = []
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

    # ---- CL #52771+ Home / End / PgUp / PgDn — 빠른 이동 ----------------

    def _first_entry_row(self) -> int:
        """첫 실거래의 DataTable row index. sentinel 보이면 1, 아니면 0."""
        return 1 if self._show_sentinel else 0

    def _last_entry_row(self) -> int:
        """마지막 실거래의 row index. entries 비면 0 (또는 sentinel 1자리)."""
        if not self._entries:
            return 0
        if self._show_sentinel:
            return len(self._entries)  # row 0 sentinel + row 1..N entries
        return len(self._entries) - 1

    def _page_step(self) -> int:
        """PgUp/PgDn 한 번에 이동할 row 수 — DataTable 의 가시 영역 높이.

        textual DataTable 의 정확한 표시 row 수가 위젯 size 와 header 처리에
        달려있어 보수적으로 가시 size.height - 1 (header) 또는 fallback 10.
        """
        try:
            table = self.query_one("#entries-table", DataTable)
            h = getattr(table.size, "height", 0) or 0
            return max(1, h - 1)
        except Exception:  # pragma: no cover
            return 10

    def action_row_home(self) -> None:
        """Home — 첫 실거래 row 로. sentinel 보이는 상태라면 sentinel(0) 이 아니라
        실 거래 첫 항목 (1) 로 — 사용자 의도와 가까움 (Home = 데이터 처음).
        """
        table = self.query_one("#entries-table", DataTable)
        target = self._first_entry_row()
        if not self._entries:
            # 빈 list — sentinel 자리 (0) 만 의미.
            target = 0
        try:
            table.move_cursor(row=target, animate=False)
        except Exception:  # pragma: no cover
            pass

    def action_row_end(self) -> None:
        """End — 마지막 실거래 row 로."""
        table = self.query_one("#entries-table", DataTable)
        target = self._last_entry_row()
        try:
            table.move_cursor(row=target, animate=False)
        except Exception:  # pragma: no cover
            pass

    def action_row_pageup(self) -> None:
        """PgUp — 한 페이지 위. 페이지 step 은 가시 영역 height."""
        table = self.query_one("#entries-table", DataTable)
        cur = table.cursor_row
        target = max(self._first_entry_row(), cur - self._page_step())
        try:
            table.move_cursor(row=target, animate=False)
        except Exception:  # pragma: no cover
            pass

    def action_row_pagedown(self) -> None:
        """PgDn — 한 페이지 아래. 마지막 entry 를 넘지 않음."""
        table = self.query_one("#entries-table", DataTable)
        cur = table.cursor_row
        target = min(self._last_entry_row(), cur + self._page_step())
        try:
            table.move_cursor(row=target, animate=False)
        except Exception:  # pragma: no cover
            pass

    # ---- CL #52773+ Shift + navigation — 범위 multi-select ---------------

    def _extend_selection_to(self, target_row: int) -> None:
        """anchor row ~ target_row 사이의 entries 를 selection 에 추가.

        anchor 가 None 이면 현재 cursor 를 anchor 로 set. 그 후 cursor 를
        target_row 로 이동 + anchor~target 범위의 entry_id 를 selection 에
        union (toggle 이 아닌 add — 사용자가 범위를 점진적으로 확장하는
        Windows/macOS 표준 패턴).
        """
        table = self.query_one("#entries-table", DataTable)
        if self._selection_anchor is None:
            self._selection_anchor = table.cursor_row
        anchor = self._selection_anchor
        lo, hi = sorted((anchor, target_row))
        for r in range(lo, hi + 1):
            idx = self._entry_index_for_row(r)
            if idx is None or idx < 0 or idx >= len(self._entries):
                continue
            eid = str(self._entries[idx].get("entry_id") or "")
            if eid:
                self._selected_entry_ids.add(eid)
        try:
            table.move_cursor(row=target_row, animate=False)
        except Exception:  # pragma: no cover
            pass
        # 행을 다시 그려 ▣ 표시 반영.
        self._render_table(self._entries, target_cursor=target_row)
        self.set_status(
            f"선택: {len(self._selected_entry_ids)}건 "
            f"(Shift+navigation 으로 범위 확장 / m 또는 # 으로 일괄 태그)",
        )

    def action_row_select_up(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        self._extend_selection_to(max(self._first_entry_row(), table.cursor_row - 1))

    def action_row_select_down(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        self._extend_selection_to(min(self._last_entry_row(), table.cursor_row + 1))

    def action_row_select_home(self) -> None:
        self._extend_selection_to(self._first_entry_row())

    def action_row_select_end(self) -> None:
        self._extend_selection_to(self._last_entry_row())

    def action_row_select_pageup(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        target = max(self._first_entry_row(), table.cursor_row - self._page_step())
        self._extend_selection_to(target)

    def action_row_select_pagedown(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        target = min(self._last_entry_row(), table.cursor_row + self._page_step())
        self._extend_selection_to(target)

    # ---- CL #52773+ 마우스 click — Ctrl / Shift modifier multi-select ----

    def on_click(self, event) -> None:
        """Ctrl+click → 토글 한 항목. Shift+click → 현재 cursor ~ 클릭 row 범위.

        modifier 없는 단순 click 은 textual DataTable 의 default (cursor 이동)
        그대로 통과 — 본 핸들러는 event 를 stop 안 하면 default 도 작동.
        """
        # event 가 DataTable cell 위에서 발생한 경우만 처리.
        try:
            table = self.query_one("#entries-table", DataTable)
        except Exception:  # pragma: no cover
            return
        widget = getattr(event, "widget", None) or getattr(event, "control", None)
        if widget is not table and not self._is_descendant(widget, table):
            return
        ctrl = bool(getattr(event, "ctrl", False))
        shift = bool(getattr(event, "shift", False))
        if not (ctrl or shift):
            # 일반 click — default cursor 이동 + anchor reset.
            self._selection_anchor = table.cursor_row
            return
        # 클릭한 row 좌표 회수 — table 의 widget-local y → row index.
        # textual 의 DataTable 은 `event.y` 가 widget 영역 기준 y offset.
        try:
            offset = event.get_content_offset(table) if hasattr(
                event, "get_content_offset",
            ) else None
            if offset is not None:
                clicked_row = table.hover_row
            else:
                clicked_row = getattr(event, "y", -1) - 1
        except Exception:  # pragma: no cover
            clicked_row = table.hover_row
        if clicked_row is None or clicked_row < 0:
            return
        if ctrl:
            # Ctrl+click — 토글 단일 항목, anchor 갱신.
            idx = self._entry_index_for_row(clicked_row)
            if idx is None or idx < 0 or idx >= len(self._entries):
                return
            eid = str(self._entries[idx].get("entry_id") or "")
            if eid:
                if eid in self._selected_entry_ids:
                    self._selected_entry_ids.discard(eid)
                else:
                    self._selected_entry_ids.add(eid)
                self._selection_anchor = clicked_row
                self._render_table(self._entries, target_cursor=clicked_row)
                self.set_status(
                    f"선택: {len(self._selected_entry_ids)}건",
                )
                event.stop()
        elif shift:
            self._extend_selection_to(clicked_row)
            event.stop()

    @staticmethod
    def _is_descendant(widget, ancestor) -> bool:
        """widget 이 ancestor 의 트리 안에 있는지 — 단순 부모 chain walk."""
        try:
            cur = widget
            while cur is not None:
                if cur is ancestor:
                    return True
                cur = getattr(cur, "parent", None)
        except Exception:  # pragma: no cover
            pass
        return False

    # ---- 컬럼 navigation (CL #51053+, 활성/비활성 상태는 #51064+) -----

    def _item_col_index(self) -> int:
        return self._COLUMN_NAMES.index("item")

    def _memo_col_index(self) -> int:
        return self._COLUMN_NAMES.index("memo")

    def _current_row_tags(self) -> list[str]:
        """현재 cursor row 의 entry 가 가진 해시태그 list — 없으면 빈 list."""
        if not self._entries:
            return []
        try:
            table = self.query_one("#entries-table", DataTable)
        except Exception:  # pragma: no cover
            return []
        idx = self._entry_index_for_row(table.cursor_row)
        if idx is None:
            return []
        eid = str(self._entries[idx].get("entry_id") or "")
        return list(self._entry_tags.get(eid, []))

    # ---- 컴팩트 모드 + 가로 스크롤 (CL #51121+) -------------------------

    def _column_is_visible(self, col_index: int) -> bool:
        """해당 컬럼이 시각상 보이는지 — `width=0` 인 컬럼은 컴팩트에서 숨김.

        Note: 컬럼 visibility 는 `_compact_level` 단계별로 다르다 (CL #51125+).
        CL #51158+ (review C4): pure logic 은 entries_compact 모듈로.
        """
        from whooing_tui.screens.entries_compact import column_is_visible
        return column_is_visible(col_index, self._compact_level)

    def _next_visible_col(self, start: int, step: int) -> int:
        """`start` 부터 `step` (+1 또는 -1) 방향으로 다음 visible 컬럼 인덱스.

        boundary 도달 시 마지막 visible 컬럼 인덱스 반환 — 사용자가 끝에서
        한 번 더 ←/→ 누르면 멈춤 (= 그대로 같은 컬럼).
        """
        last_visible = start
        i = start + step
        while 0 <= i < len(self._COLUMN_NAMES):
            if self._column_is_visible(i):
                return i
            i += step
        return last_visible

    def _scroll_active_col_into_view(self) -> None:
        """좁은 터미널에서 활성 컬럼이 화면 밖에 있으면 가로 스크롤.

        CL #51121+ 사용자 보고: blink 터미널 ←/→ 으로 이동하다 화면 밖
        컬럼을 가리키면 marker 가 보이지 않아 어떤 컬럼이 활성인지 알 수
        없음. DataTable 내부 `_get_cell_region(coord)` + `scroll_to_region`
        으로 cell 을 가시 영역으로 끌어옴.
        """
        if not self._column_active or not self._entries:
            return
        try:
            table = self.query_one("#entries-table", DataTable)
        except Exception:  # pragma: no cover
            return
        coord = Coordinate(table.cursor_row, self._active_col)
        try:
            region = table._get_cell_region(coord)
            table.scroll_to_region(region, force=True, animate=False)
        except Exception:  # pragma: no cover — internal API 변경 등
            log.debug("scroll_to_region failed", exc_info=True)

    def action_prev_column(self) -> None:
        """← 키 — marker 비활성이면 활성화만 (_active_col 그대로), 활성이면 -1.

        CL #51102+: memo 위에서 ← 면 그 row 가 태그 가지고 있으면 마지막 태그
        를 선택 (`_active_col=item, _tag_index=N-1`). item 위에서 태그 모드
        인 동안엔 `_tag_index -= 1`; 0 에서 다시 ← 면 태그 모드 종료 (item
        셀 자체 marker 로 복귀).

        CL #51121+: 컴팩트 모드 (`_compact=True`) 에서는 hidden 컬럼 (left
        /right/memo) 을 자동 skip — 좁은 터미널에서도 visible 한 컬럼만 순
        회. 이동 후 활성 컬럼을 가로 스크롤로 화면 안에.
        """
        if not self._column_active:
            self._column_active = True
            self._update_active_cell_marker()
            self._announce_active_column()
            self._scroll_active_col_into_view()
            return

        item_idx = self._item_col_index()
        memo_idx = self._memo_col_index()
        # memo → 그 row 의 마지막 태그 (있으면).
        if self._active_col == memo_idx and self._tag_index is None:
            tags = self._current_row_tags()
            if tags:
                self._active_col = item_idx
                self._tag_index = len(tags) - 1
                self._update_active_cell_marker()
                self._announce_active_column()
                self._scroll_active_col_into_view()
                return
        # 태그 모드에서 한 칸 이전 — 0 까지.
        if self._active_col == item_idx and self._tag_index is not None:
            if self._tag_index > 0:
                self._tag_index -= 1
            else:
                # 태그 모드 종료 → item 셀 자체 marker.
                self._tag_index = None
            self._update_active_cell_marker()
            self._announce_active_column()
            self._scroll_active_col_into_view()
            return
        # 일반 컬럼 -1 (컴팩트 모드 hidden col 은 skip).
        if self._active_col > 0:
            new_col = self._next_visible_col(self._active_col, -1)
            if new_col != self._active_col:
                self._active_col = new_col
                self._tag_index = None
                self._update_active_cell_marker()
                self._announce_active_column()
                self._scroll_active_col_into_view()

    def action_next_column(self) -> None:
        """→ 키 — marker 비활성이면 활성화만, 활성이면 +1.

        CL #51102+: item 위 + 태그가 있으면 → 한 번 더 누름이 태그 모드 진입
        (`_tag_index=0`). 마지막 태그에서 → 면 태그 모드 종료 + memo 로.
        CL #51121+: 컴팩트 모드 hidden 컬럼 자동 skip + 가로 스크롤.
        """
        if not self._column_active:
            self._column_active = True
            self._update_active_cell_marker()
            self._announce_active_column()
            self._scroll_active_col_into_view()
            return

        item_idx = self._item_col_index()
        memo_idx = self._memo_col_index()
        # item → 태그 모드 진입 (있을 때).
        if self._active_col == item_idx and self._tag_index is None:
            tags = self._current_row_tags()
            if tags:
                self._tag_index = 0
                self._update_active_cell_marker()
                self._announce_active_column()
                self._scroll_active_col_into_view()
                return
        # 태그 모드 → 다음 태그 또는 memo.
        if self._active_col == item_idx and self._tag_index is not None:
            tags = self._current_row_tags()
            if self._tag_index + 1 < len(tags):
                self._tag_index += 1
            else:
                # 태그 모드 종료 + memo 진입 (컴팩트면 visible memo 로 skip).
                self._tag_index = None
                # memo 가 컴팩트에서 hidden 이므로 next_visible 로 jump.
                self._active_col = self._next_visible_col(memo_idx - 1, +1)
            self._update_active_cell_marker()
            self._announce_active_column()
            self._scroll_active_col_into_view()
            return
        # 일반 컬럼 +1 (컴팩트 모드 hidden col 은 skip).
        if self._active_col < len(self._COLUMN_NAMES) - 1:
            new_col = self._next_visible_col(self._active_col, +1)
            if new_col != self._active_col:
                self._active_col = new_col
                self._tag_index = None
                self._update_active_cell_marker()
                self._announce_active_column()
                self._scroll_active_col_into_view()

    def action_deactivate_column(self) -> None:
        """Esc — 활성 컬럼 marker + 활성 필터 + multi-select 를 함께 해제.

        모두 비활성이면 noop (앱 종료 X — 사용자 지시).

        사용자 지시 이력:
          * CL #51064: "ESC를 누르면 오렌지색 커서만 선택취소… 파란색
            커서만 있는 상태에서 ESC는 아무 동작도 하지 않습니다. ESC로
            종료되지 않게 해주세요. 종료키는 q 입니다."
          * CL #51068: "오렌지색 커서로 필터링이 적용된 상태에서 ESC 키
            를 누르면 커서 하이라이트 해제 및 동시에 필터도 해제."
          * CL #52781 (현 변경): "여러 항목이 선택된 상태에서 ESC 키를
            누르면 선택 항목이 취소되게 해주세요."

        결합 정의: 활성인 것들을 모두 한 번에 해제. 모두 비활성 → noop.
        """
        had_filter = self._active_filter is not None
        had_selection = bool(self._selected_entry_ids)
        if (
            not self._column_active and not had_filter and not had_selection
        ):
            return  # noop — 모두 비활성

        if self._column_active:
            self._column_active = False
            self._tag_index = None  # CL #51102+: 태그 모드도 같이 해제
            self._update_active_cell_marker()  # marker cleanup

        if had_filter:
            self._active_filter = None
            self._entries = list(self._all_entries)

        # CL #52781+: multi-select 해제 — anchor 도 reset.
        if had_selection:
            self._selected_entry_ids.clear()
            self._selection_anchor = None

        # 변경이 있으면 re-render (필터 / selection 둘 다 셀 갱신 필요).
        if had_filter or had_selection:
            # _render_table 끝의 _update_active_cell_marker 가 _column_active
            # 비활성이라 marker 재적용 X — 깨끗한 plain table 로 그려진다.
            self._render_table(self._entries)

        # status 안내 — 어떤 게 해제됐는지 명시.
        parts: list[str] = []
        if self._column_active is False and had_filter:
            parts.append("컬럼 / 필터")
        elif had_filter:
            parts.append("필터")
        elif self._column_active is False:
            parts.append("컬럼")
        if had_selection:
            parts.append("선택")
        if parts:
            self.set_status(f"{' / '.join(parts)} 해제.")

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted,
    ) -> None:
        """↑/↓ 또는 click 으로 cursor row 가 바뀌면 marker 도 따라 이동.

        sentinel row (0) 에 cursor 가 가면 marker 가 자동으로 cleanup
        되고, status 에 "Enter = 새 거래 추가" 안내. `_column_active=False`
        이면 _update_active_cell_marker 가 알아서 early return.

        CL #51096+: cursor 가 sentinel 위에 있는 동안 table 에 `.sentinel-
        active` class 를 토글 — CSS 가 cursor cell 의 배경/글자색을 노란/
        검정 으로 바꿔 일반 거래 row 와 시각적으로 구분.

        CL #51102+: row 가 바뀌면 태그 모드 자동 종료. 각 row 의 태그 개수
        가 달라 `_tag_index` 를 보존하는 게 의미 없다 (사용자 답변 명시).
        """
        if self._tag_index is not None:
            self._tag_index = None
        self._update_active_cell_marker()
        self._update_sentinel_cursor_class()
        if self._is_on_sentinel_row():
            self.set_status("[Enter = 새 거래 추가]")

    def _update_sentinel_cursor_class(self) -> None:
        """sentinel row 위에서만 `.sentinel-active` class 를 table 에 부여.

        row_highlighted 이벤트 + `_render_table` 마지막 양쪽에서 호출 —
        초기 빈 entries 처럼 cursor 이동이 없는 케이스도 일관되게 갱신.
        """
        try:
            table = self.query_one("#entries-table", DataTable)
        except Exception:  # pragma: no cover — 화면 unmount 직후
            return
        if self._is_on_sentinel_row():
            table.add_class("sentinel-active")
        else:
            table.remove_class("sentinel-active")

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

        # CL #51102+: 태그 모드면 item 셀을 일반 marker 대신 *그 안의 한
        # 태그만* cyan 으로 강조해 cell 자체를 다시 build.
        if (
            self._tag_index is not None
            and cur_col == self._item_col_index()
        ):
            marked = self._render_item_cell_with_tag_marker(
                self._entries[cur_entry_idx], self._tag_index,
            )
            try:
                table.update_cell_at(
                    Coordinate(cur_row, cur_col),
                    marked,
                    update_width=False,
                )
            except Exception:  # pragma: no cover
                return
            self._marked_cell = (cur_row, cur_col)
            return

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
        # CL #51102+: 태그 모드면 별도 안내.
        if self._tag_index is not None:
            tags = self._current_row_tags()
            if 0 <= self._tag_index < len(tags):
                tag = tags[self._tag_index]
                self.set_status(
                    f"활성 태그: #{tag}    Enter = 같은 태그로 필터",
                )
                return
        col = self._COLUMN_NAMES[self._active_col]
        if col == "memo":
            # CL #52757+: memo 는 substring 매칭 — 사용자 직관과 일치.
            hint = "Enter = 비슷한 memo 로 필터 (키워드 substring)"
        elif col in FILTERABLE_COLUMNS:
            hint = f"Enter = 같은 {col} 으로 필터"
        elif col == "money":
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
          * **태그 모드 (CL #51102+)**: 그 태그로 entries 필터.
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
        # CL #51102+: 태그 모드 → 그 태그로 필터.
        if self._tag_index is not None:
            tags = self._current_row_tags()
            if 0 <= self._tag_index < len(tags):
                self._apply_tag_filter(tags[self._tag_index])
            return
        col = self._COLUMN_NAMES[self._active_col]
        if col in FILTERABLE_COLUMNS:
            # CL #52757+: memo 도 FILTERABLE — substring 매칭.
            self._apply_filter(col, target)
        else:
            # money 컬럼 → edit_entry.
            self.action_edit_entry()

    def _apply_tag_filter(self, tag: str) -> None:
        """CL #51102+: 해당 tag 가 붙은 entries 만 보여준다 (`_entry_tags`
        사전 lookup). 다른 column 필터와 같은 status / 해제 흐름.

        `_active_filter` 의 식별자는 `("tag", {"tag": tag})` 로 통일 — 기존
        column 기반 필터 path 와 분기되도록 column key 를 `"tag"` 로 사용.
        """
        wanted = (tag or "").strip().lstrip("#")
        if not wanted:
            return
        filtered = [
            e for e in self._all_entries
            if wanted in (
                self._entry_tags.get(str(e.get("entry_id") or ""), [])
            )
        ]
        if not filtered:
            self.set_status(
                f"태그 필터 '#{wanted}' — 매칭 0건. c 로 해제 / r 로 재로드.",
                warn=True,
            )
            return
        self._active_filter = ("tag", {"tag": wanted})
        self._entries = filtered
        self._render_table(filtered)
        self.set_status(
            f"필터: tag=#{wanted} — {len(filtered)}/{len(self._all_entries)}건. "
            f"c 로 해제 / r 로 재로드.",
            warn=True,
        )

    def _apply_filter(self, column: str, target: dict[str, Any]) -> None:
        """CL #52758+: 점진적 확장 필터.

        흐름:
          1. 현재 윈도우 (_all_entries) 의 매칭 즉시 표시.
          2. **sqlite 캐시 lookup** — 윈도우 밖의 캐시된 entries 중 매칭 추가
             (`_filter_extra`). 결과 즉시 화면 반영.
          3. **background worker** — 캐시보다 더 과거를 후잉 API 로 점진적
             fetch (-3m / -6m / -12m / -24m step) → 캐시 upsert → 매칭만
             `_filter_extra` 에 누적 → 매 step UI / status 갱신.

        필터 해제 (`c` / `Esc`) 또는 새 필터 시작 시 `_filter_epoch` 증가 →
        진행 중 worker 가 자기 결과를 폐기 (race 방지).
        """
        filtered_window = filter_entries(self._all_entries, column, target)
        # 새 필터 시작 — 이전 worker 의 결과 무시.
        self._filter_epoch += 1
        self._filter_extra = []
        self._active_filter = (column, target)

        # 1. 캐시에서 매칭 추가 (현재 윈도우 entry_id 는 제외 — 중복 방지).
        cache_extras = self._fetch_cache_extras(column, target)

        all_filtered = self._combine_filter_results(filtered_window, cache_extras)
        self._filter_extra = list(cache_extras)
        self._entries = all_filtered
        self._render_table(all_filtered)

        label = self._filter_label(column, target)
        if not all_filtered:
            self.set_status(
                f"'{label}' — 매칭 0건. c 로 해제 / r 로 재로드.",
                warn=True,
            )
            # 매칭 0건이라도 worker 는 띄움 — 과거 데이터에서 발견될 수 있음.
        else:
            self.set_status(
                f"필터: {label} — {len(all_filtered)}건 "
                f"(현재 {len(filtered_window)} + 캐시 {len(cache_extras)}). "
                f"과거 데이터 검색 중…",
                warn=True,
            )

        # 2. 과거로 확장 — worker 띄움.
        self._expand_filter_in_past(self._filter_epoch, column, target)

    def _fetch_cache_extras(
        self, column: str, target: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """sqlite 캐시에서 _all_entries 밖의 매칭만 반환.

        실패 (db 없음 / 권한) 는 빈 list — 사용자에게 표면화 X.
        """
        session = self.app.session  # type: ignore[attr-defined]
        sid = session.section_id or None
        if not sid:
            return []
        try:
            from whooing_core import entries_cache as core_cache
            from whooing_tui import data as tui_data

            current_ids = {
                str(e.get("entry_id") or "") for e in self._all_entries
                if e.get("entry_id")
            }
            with tui_data.open_ro() as conn:
                # _all_entries 윈도우 밖 (= 그 이전) 의 캐시만.
                window_oldest = self._window_oldest_yyyymmdd()
                cached = core_cache.list_cached(
                    conn, sid,
                    end_date=_yesterday_of(window_oldest) if window_oldest else None,
                    exclude_entry_ids=current_ids,
                )
        except Exception:  # pragma: no cover
            log.exception("cache lookup failed")
            return []
        return filter_entries(cached, column, target)

    def _window_oldest_yyyymmdd(self) -> str | None:
        """_all_entries 의 가장 오래된 entry_date 8자리 (캐시 분기점)."""
        from whooing_tui.filters import date_head
        oldest: str | None = None
        for e in self._all_entries:
            d = date_head(e.get("entry_date"))
            if not d:
                continue
            if oldest is None or d < oldest:
                oldest = d
        return oldest

    def _combine_filter_results(
        self,
        window: list[dict[str, Any]],
        extras: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """현재 윈도우 매칭 + 추가 매칭을 entry_date desc 정렬해 합산.

        같은 entry_id 가 양쪽에 있으면 윈도우 (= 후잉 최신) 우선.
        """
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for e in window:
            eid = str(e.get("entry_id") or "")
            if eid:
                seen.add(eid)
            out.append(e)
        for e in extras:
            eid = str(e.get("entry_id") or "")
            if eid and eid in seen:
                continue
            out.append(e)
            if eid:
                seen.add(eid)
        out.sort(
            key=lambda e: (e.get("entry_date") or "", str(e.get("entry_id") or "")),
            reverse=True,
        )
        return out

    @work(exclusive=True, group="filter_expand", name="expand_filter")
    async def _expand_filter_in_past(
        self,
        epoch: int,
        column: str,
        target: dict[str, Any],
    ) -> None:
        """필터 결과를 과거로 점진적 확장.

        step list 의 각 단계마다:
          - 후잉 list_entries(window) 호출
          - 캐시 upsert (다음 호출 시 가속화)
          - 필터 매칭만 _filter_extra 에 누적
          - UI / status 갱신
        사용자가 필터를 변경/해제하면 epoch mismatch → 결과 폐기.
        """
        from whooing_core import entries_cache as core_cache
        from whooing_tui import data as tui_data

        session = self.app.session  # type: ignore[attr-defined]
        sid = session.section_id
        if not sid:
            return

        # step boundary 후보 — `WHOOING_FILTER_EXPAND_MONTHS` env 로 override
        # 가능 ("3,6,12,24" 등 콤마). 미지정 시 constants 의 default
        # (CL #52858+: 60 개월까지 — 종전 24 개월에서 확장. 사용자 보고:
        # "2024년 이전 정보가 안 나옴" → 오늘 - 24 개월 도달 한계).
        steps_default = list(constants.FILTER_EXPAND_STEP_MONTHS)
        steps_env = os.getenv("WHOOING_FILTER_EXPAND_MONTHS")
        if steps_env:
            try:
                steps = [int(x) for x in steps_env.split(",") if x.strip()]
            except ValueError:
                steps = steps_default
        else:
            steps = steps_default

        # 시작점 — _all_entries 의 가장 오래된 날짜.
        window_oldest = self._window_oldest_yyyymmdd() or today_yyyymmdd()

        for months in steps:
            if epoch != self._filter_epoch:
                return  # 사용자가 필터 변경 — 폐기.
            # 윈도우: (today - months) ~ window_oldest 직전.
            end_d = _yesterday_of(window_oldest) or window_oldest
            start_d = days_ago_yyyymmdd(months * 30)
            if start_d >= end_d:
                continue
            try:
                fetched = await self._client.list_entries(sid, start_d, end_d)
            except Exception:
                log.exception("filter expand fetch failed at -%d months", months)
                continue
            if epoch != self._filter_epoch:
                return

            # 캐시 upsert (다음 호출 가속화).
            try:
                with tui_data.open_rw() as conn:
                    core_cache.upsert_entries(conn, sid, fetched)
            except Exception:  # pragma: no cover
                log.exception("cache upsert failed")

            # 필터 매칭만 추가. 이미 누적된 entry_id 는 제외.
            current_ids = {
                str(e.get("entry_id") or "") for e in self._all_entries
            } | {
                str(e.get("entry_id") or "") for e in self._filter_extra
            }
            new_matches = [
                e for e in filter_entries(fetched, column, target)
                if str(e.get("entry_id") or "") not in current_ids
            ]
            if new_matches:
                self._filter_extra.extend(new_matches)
                # 화면 갱신.
                window_filtered = filter_entries(
                    self._all_entries, column, target,
                )
                combined = self._combine_filter_results(
                    window_filtered, self._filter_extra,
                )
                self._entries = combined
                self._render_table(combined)
            label = self._filter_label(column, target)
            self.set_status(
                f"필터: {label} — {len(self._entries)}건 "
                f"(과거 {months}개월 확인 — {start_d}~{end_d}). "
                f"c 로 해제 / r 로 재로드.",
                warn=True,
            )

            # 다음 step 의 end 는 이번 step 의 start.
            window_oldest = start_d

        # 모든 step 완료.
        if epoch != self._filter_epoch:
            return
        label = self._filter_label(column, target)
        self.set_status(
            f"필터: {label} — {len(self._entries)}건 (과거 {steps[-1]}개월 까지 검색 완료). "
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
        if column == "memo":  # CL #52757+
            from whooing_tui.filters import memo_keywords
            keys = memo_keywords(target.get("memo"))
            return f"memo∋{{{', '.join(sorted(keys))}}}"
        if column == "tag":  # CL #51102+
            return f"tag=#{target.get('tag') or '?'}"
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
        self._window_days = min(
            constants.MAX_WINDOW_DAYS,
            self._window_days + constants.WINDOW_STEP_DAYS,
        )
        self.set_status(
            f"윈도우 +{constants.WINDOW_STEP_DAYS}일 → 최근 "
            f"{self._window_days}일. 재로드 중…"
        )
        self.refresh_entries()

    def action_shrink_window(self) -> None:
        self._window_days = max(
            constants.MIN_WINDOW_DAYS,
            self._window_days - constants.WINDOW_STEP_DAYS,
        )
        self.set_status(
            f"윈도우 -{constants.WINDOW_STEP_DAYS}일 → 최근 "
            f"{self._window_days}일. 재로드 중…"
        )
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

    def action_show_context_menu(self) -> None:
        """CL #52763+: 'm' (또는 ㅡ) — 선택 거래의 context menu.

        사용자 요청: m 키로 메뉴를 열어 그 안에서 삭제할 수 있도록.
        메뉴 항목은 거래 row 에서 자주 쓰는 액션들 — 수정/삭제/첨부/새 거래
        + multi-select 기반 일괄 작업 (선택돼 있을 때만).

        sentinel row (새 거래 자리) / entry_id 없는 거래는 메뉴 항목이
        의미가 없으므로 status 안내 후 noop.
        """
        from whooing_tui.widgets.menubar import MenuItem, MenuPopup, MenuSpec

        if self._is_on_sentinel_row():
            self.set_status(
                "새 거래 자리 — Enter 또는 n 으로 거래 추가.", warn=True,
            )
            return
        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        eid = str(target.get("entry_id") or "")
        if not eid:
            self.set_status("이 거래에는 entry_id 가 없습니다.", error=True)
            return

        items: list[MenuItem] = [
            MenuItem(label="수정 (e)", action_id="edit_entry"),
            MenuItem(label="삭제 (d)", action_id="delete_entry"),
            MenuItem(label="첨부 (f)", action_id="open_attachments"),
            MenuItem(label="새 거래 (n)", action_id="new_entry"),
        ]
        # multi-select 가 1+ 면 일괄 태그 항목 추가 (CL #51145+).
        if self._selected_entry_ids:
            items.append(MenuItem(
                label=f"선택 {len(self._selected_entry_ids)}건 일괄 태그 (#)",
                action_id="batch_tag",
            ))
        # CL #52815+: 2건 이상 선택 시 '중복인지 평가' — 사용자 요청.
        # 1건만 선택돼있으면 비교 대상이 없어 의미 없으므로 미노출.
        if len(self._selected_entry_ids) >= 2:
            items.append(MenuItem(
                label=f"선택 {len(self._selected_entry_ids)}건 중복인지 평가…",
                action_id="evaluate_duplicates",
            ))
        spec = MenuSpec(name="거래", items=tuple(items))

        def _on_pick(result: Any) -> None:
            # MenuPopup 의 nav (←/→) 결과는 context 에서 의미 없음 — 무시.
            if result is None or isinstance(result, tuple):
                return
            method = getattr(self, f"action_{result}", None)
            if callable(method):
                method()
            else:  # pragma: no cover — items 의 action_id 와 메서드 매칭 보장.
                log.debug("context menu: action_%s 없음", result)

        self.app.push_screen(MenuPopup(spec), _on_pick)

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
        """edit dialog 진입 직전 로컬 db 에서 해시태그 prefill.

        CL #52834+: EntryRepository.tags_for() 로 위임. 본 메서드는 후방
        호환 wrapper — 다른 코드가 본 이름으로 호출 중.
        """
        return self._repo.tags_for(entry_id)

    def _render_item_cell_with_tag_marker(
        self, entry: dict[str, Any], tag_idx: int,
    ) -> str:
        """태그 모드 marker 렌더 — item 셀 안에서 tag_idx 번째 태그만 cyan
        강조. 다른 토큰은 plain. 화면 밖에 잘려도 안전한 일반 markup 형식.
        """
        item_text = entry.get("item") or ""
        tags = self._entry_tags.get(str(entry.get("entry_id") or "")) or []
        # CL #52777+: limit==0 면 모든 태그 표시 (사용자 요청).
        limit = self._item_tag_inline_limit()
        if limit > 0 and len(tags) > limit:
            shown = tags[:limit]
            extra = len(tags) - limit
        else:
            shown = tags
            extra = 0
        parts: list[str] = []
        if item_text:
            parts.append(item_text)
        for i, t in enumerate(shown):
            tok = f"#{t}"
            if i == tag_idx:
                parts.append(f"[{self._TAG_MARKER_STYLE}]{tok}[/]")
            else:
                parts.append(tok)
        if extra > 0:
            tok = f"#…({extra})"
            # 축약 토큰은 marker 대상 외 — 사용자가 그 위에서 enter 해도
            # 지정할 단일 태그가 없다.
            parts.append(tok)
        return " ".join(parts)

    def _fetch_all_entry_tags(self, entry_ids: list[str]) -> dict[str, list[str]]:
        """CL #52834+: EntryRepository.tags_for_many() 로 위임."""
        return self._repo.tags_for_many(entry_ids)

    def _fetch_all_attachment_counts(
        self, entry_ids: list[str],
    ) -> dict[str, int]:
        """CL #52834+: EntryRepository.attachment_counts() 로 위임 (활성 섹션 격리)."""
        session = self.app.session  # type: ignore[attr-defined]
        return self._repo.attachment_counts(
            entry_ids, section_id=session.section_id or None,
        )

    def _fetch_tag_colors(self) -> dict[str, str]:
        """CL #52834+: EntryRepository.tag_colors() 로 위임."""
        session = self.app.session  # type: ignore[attr-defined]
        return self._repo.tag_colors(section_id=session.section_id or None)

    def _fetch_all_tags_db(self) -> dict[str, int]:
        """CL #52834+: EntryRepository.all_tags() 로 위임."""
        return self._repo.all_tags()

    def _persist_local(
        self,
        *,
        entry_id: str,
        section_id: str,
        memo: str,
        tags: list[str],
    ) -> None:
        """CL #52834+: EntryRepository.persist() 로 위임.

        본 wrapper 의 keyword 시그니처는 후방 호환 — 기존 호출자가 본 이름
        + kwargs 로 부르기 때문. repo 는 동일 시그니처.
        """
        self._repo.persist(
            entry_id=entry_id,
            section_id=section_id,
            memo=memo,
            tags=tags,
        )

    def _purge_local(self, entry_id: str) -> None:
        """CL #52834+: EntryRepository.purge() 로 위임 — 후방 호환 wrapper."""
        self._repo.purge(entry_id)

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
        # CL #52758+: refresh 직후 캐시 upsert — 다음 필터 / 부팅 가속화.
        # 진행 중이던 _expand_filter_in_past worker 의 결과는 새 윈도우
        # 기준으로 다시 계산하도록 epoch bump (사용자가 r 키 / refresh 시).
        self._filter_epoch += 1
        self._filter_extra = []
        try:
            from whooing_core import entries_cache as core_cache
            with tui_data.open_rw() as conn:
                core_cache.upsert_entries(conn, section_id, entries_sorted)
        except Exception:  # pragma: no cover
            log.exception("entries_cache upsert failed (refresh)")
        # CL #51102+: 로컬 sqlite 의 해시태그를 batch fetch 해 entry_id →
        # tag list 사전 보관. _format_cell 의 item 컬럼에 인라인 표시 + 태그
        # 단위 column 네비 + tag 필터 의 단일 source.
        eids = [str(e.get("entry_id") or "") for e in entries_sorted if e.get("entry_id")]
        self._entry_tags = self._fetch_all_entry_tags(eids)
        # CL #51134+ (A6): item 컬럼에 📎N prefix 표시 — 첨부 발견성.
        self._entry_attachment_counts = self._fetch_all_attachment_counts(eids)
        # CL #51151+ (H11): 태그 색 batch fetch — inline 표시에 적용.
        self._tag_colors = self._fetch_tag_colors()
        # CL #51145+ (H6): 사라진 entry 의 stale selection 정리.
        valid_eids = set(eids)
        self._selected_entry_ids &= valid_eids
        self._render_table(entries_sorted)
        self._update_window_status(start_date, end_date, entries_sorted)

    # ---- render --------------------------------------------------------

    # CL #51102+: item 셀 안에 인라인 태그가 너무 많아질 때 보여줄 최대 개수.
    # 그 이상은 마지막 옵션으로 `#…(N)` 한 토큰만 추가.
    # CL #51141+ (H9): 환경 변수 `WHOOING_ITEM_TAG_INLINE_LIMIT` 로 override.
    # CL #52777+ (사용자 요청 "태그를 모두 보여주세요"): default 를 0
    # (무제한, 전체 표시) 으로 변경. 좁은 터미널에서는 환경변수로 cap 가능.
    @classmethod
    def _item_tag_inline_limit(cls) -> int:
        import os
        raw = os.getenv("WHOOING_ITEM_TAG_INLINE_LIMIT")
        if raw:
            try:
                return max(0, int(raw))
            except ValueError:
                pass
        return cls._ITEM_TAG_INLINE_LIMIT

    # 0 = 무제한 (모든 태그 표시). >0 = 그 개수만 + `#…(N)`.
    _ITEM_TAG_INLINE_LIMIT = 0

    @staticmethod
    def _highlight_selected_cell(cell: Any) -> Any:
        """CL #52781+: multi-select 된 row 의 cell 시각 강조.

        Rich `Text` (money) 와 `str` 양쪽 모두 처리:
          - Text 면 stylize 로 `bold reverse` 적용 (justify 유지).
          - str 이면 `[bold reverse]...[/]` markup 으로 wrap.

        `▣` prefix 와 함께 row 전체가 background 반전 + bold — 사용자 캡처
        시 한눈에 구분.
        """
        if isinstance(cell, Text):
            new = cell.copy() if hasattr(cell, "copy") else Text.from_markup(str(cell))
            new.stylize("bold reverse")
            return new
        s = str(cell) if cell is not None else ""
        if not s:
            return s
        return f"[bold reverse]{s}[/bold reverse]"

    def _format_cell(self, entry: dict[str, Any], col_index: int) -> Any:
        """entry 와 column index 로부터 cell 의 표시 값을 만든다.

        `_render_table` 의 row 추가 + `_update_active_cell_marker` 의 cell
        복원 양쪽에서 같은 형식으로 보이도록 단일 helper. money 만 Rich
        `Text` 로 (오른쪽 정렬, CL #51087+) — 나머지는 `str`.

        CL #51102+: item 컬럼은 끝에 해시태그 인라인 (`스타벅스 #식비 #저녁`).
        item 이 비어있으면 태그만, 태그가 많으면 앞 N + `#…(K)` 축약. 태그
        앞의 `#` 는 시각 구분용 prefix — 실 db 에는 bare 토큰만 저장.
        """
        session = self.app.session  # type: ignore[attr-defined]
        col = self._COLUMN_NAMES[col_index]
        lvl = self._compact_level
        if col == "date":
            return _fmt_date(entry.get("entry_date"))
        if col == "money":
            return Text(_fmt_money(entry.get("money")), justify="right")
        if col == "left":
            # CL #51125+: lvl >= 4 면 hidden — 빈 문자열, lvl >= 2 면 약어.
            if lvl >= 4:
                return ""
            l_id = entry.get("l_account_id") or ""
            full = session.title_of(l_id) if l_id else ""
            return self._abbreviate_account_name(full) if lvl >= 2 else full
        if col == "right":
            if lvl >= 3:
                return ""
            r_id = entry.get("r_account_id") or ""
            full = session.title_of(r_id) if r_id else ""
            return self._abbreviate_account_name(full) if lvl >= 2 else full
        if col == "item":
            item_text = entry.get("item") or ""
            eid = str(entry.get("entry_id") or "")
            # CL #51134+ (A6): 첨부 indicator.
            attach_n = self._entry_attachment_counts.get(eid, 0)
            attach_prefix = f"📎{attach_n} " if attach_n > 0 else ""
            # CL #51145+ (H6): selection indicator. CL #52781+: 더 시각적
            # 으로 (`▣` 만으로는 사용자에게 약함, 캡처 보고). emoji + 공백.
            sel_prefix = "✅ " if eid in self._selected_entry_ids else ""
            attach_prefix = sel_prefix + attach_prefix
            tags = self._entry_tags.get(eid) or []
            if not tags:
                return f"{attach_prefix}{item_text}"
            limit = self._item_tag_inline_limit()
            # CL #52777+: limit==0 면 무제한 — 모든 태그 표시.
            if limit > 0 and len(tags) > limit:
                shown = tags[:limit]
                extra = len(tags) - limit
            else:
                shown = tags
                extra = 0
            # CL #51151+ (H11): 태그 색 적용 (Rich markup).
            tag_tokens: list[str] = []
            for t in shown:
                color = self._tag_colors.get(t)
                if color:
                    tag_tokens.append(f"[{color}]#{t}[/{color}]")
                else:
                    tag_tokens.append(f"#{t}")
            if extra > 0:
                tag_tokens.append(f"#…({extra})")
            tag_str = " ".join(tag_tokens)
            return f"{attach_prefix}{item_text} {tag_str}".strip()
        if col == "memo":
            # CL #51125+: lvl >= 1 부터 memo 숨김 (사용자 요청 1단계).
            if lvl >= 1:
                return ""
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

        # CL #51102+: 새 데이터로 그리는 시점에 태그 모드 자동 종료 — 같은
        # row 라도 태그가 변했을 수 있고 (필터 / refresh / 입력 후) 다른
        # row 의 태그 개수와도 의미가 어긋난다.
        self._tag_index = None

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
            # CL #52781+ (사용자 요청 "선택한 항목이 눈에 더 잘 띄게"):
            # multi-select 된 row 의 모든 cell 에 reverse 색반전 + bold —
            # ▣ prefix 외에 row 자체 시각 강조.
            eid = str(e.get("entry_id") or "")
            if eid and eid in self._selected_entry_ids:
                cells = [self._highlight_selected_cell(c) for c in cells]
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

        # render 후 marker 재적용 (sentinel 아닌 row 에서만) + sentinel
        # cursor 색상 클래스 동기화. row_highlighted 이벤트가 발화하지 않는
        # edge case (예: cursor 가 같은 row 에 머무는 빈 entries 부팅) 도
        # 안전하게 갱신.
        self._marked_cell = None
        self._update_active_cell_marker()
        self._update_sentinel_cursor_class()

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


# ---- CL #51126+ 일반 파일 경로 입력 모달 (메뉴 wizard 등 재사용) -----------


class _FilePathModal(ModalScreen[str | None]):
    """절대 경로를 한 줄 입력받는 단순 modal. 메뉴 wizard 등에서 재사용.

    `attachment_browser._AddPathModal` 과 형태가 비슷하지만 본 모듈의 메뉴
    wizard 가 자체 진입점으로 사용 (지역 import 회피).
    """

    BINDINGS = [Binding("escape", "cancel", "취소")]

    DEFAULT_CSS = """
    _FilePathModal {
        align: center middle;
    }
    #filepath_box {
        background: $panel;
        border: thick $primary;
        padding: 1;
        width: 95%;
        max-width: 80;
        min-width: 30;
        height: auto;
    }
    """

    def __init__(
        self,
        *,
        title: str = "파일 경로",
        placeholder: str = "/path/to/file",
    ) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Container(id="filepath_box"):
            yield Label(self._title)
            yield Input(placeholder=self._placeholder, id="filepath_input")
            with Horizontal():
                yield Button("OK", id="filepath_ok", variant="primary")
                # CL #51139+ (A7): Browse 버튼 — FilePickerScreen 으로 navigation.
                yield Button("Browse…", id="filepath_browse")
                yield Button("Cancel", id="filepath_cancel")

    def on_mount(self) -> None:
        try:
            self.query_one("#filepath_input", Input).focus()
        except Exception:  # pragma: no cover
            pass

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss((event.value or "").strip() or None)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "filepath_ok":
            value = self.query_one("#filepath_input", Input).value.strip()
            self.dismiss(value or None)
        elif event.button.id == "filepath_browse":
            from whooing_tui.screens.file_picker import FilePickerScreen
            chosen = await self.app.push_screen_wait(FilePickerScreen(
                title=self._title,
            ))
            if chosen:
                # 사용자가 picker 에서 선택했으면 바로 dismiss.
                self.dismiss(chosen)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
