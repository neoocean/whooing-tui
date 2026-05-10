"""좁은 터미널 (iPhone Blink 등) 대응 회귀 테스트 (CL #51120+).

사용자 답변에서 우선시한 폭: iPhone 세로 ~40-50 cells.

검증 항목:
- 모든 ModalScreen 의 frame 이 좁은 터미널에서 화면 안에 들어감 (오버
  플로우 X).
- EntriesScreen 이 컴팩트 모드 임계값 (`_NARROW_THRESHOLD`) 미만 size 에서
  `_compact = True` + left/right/memo 컬럼 width=0.
- 정상 size 에서는 컴팩트 X (회귀 방지).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from whooing_tui.app import WhooingTuiApp


class _Client:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"assets": [{"account_id": "x11", "title": "현금"}]}
        self._entries: list[dict[str, Any]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def create_entry(self, **kw):
        return {"entry_id": "n", **kw}

    async def update_entry(self, **kw):
        return {**kw}

    async def delete_entry(self, **kw):
        return {}


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


# ---- Phase 1: 모달 반응형 width -----------------------------------------


@pytest.mark.asyncio
async def test_entry_edit_dialog_fits_narrow_terminal():
    """40-cell 터미널에서 EntryEditDialog frame 이 화면 안에 들어가야."""
    from whooing_tui.screens.edit_entry import EntryEditDialog
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.state import SessionState

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        await app.push_screen(EntryEditDialog(app.session))
        await pilot.pause()
        frame = app.screen.query_one("#dialog-frame")
        # frame width 가 터미널 width 안에 들어가야.
        assert frame.region.width <= 40
        # 너무 작아도 안 됨 (min-width 30 보장).
        assert frame.region.width >= 30


@pytest.mark.asyncio
async def test_entry_edit_dialog_uses_max_width_on_wide_terminal():
    """120-cell 터미널에서는 max-width (76) 으로 cap."""
    from whooing_tui.screens.edit_entry import EntryEditDialog
    from whooing_tui.screens.entries import EntriesScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        await app.push_screen(EntryEditDialog(app.session))
        await pilot.pause()
        frame = app.screen.query_one("#dialog-frame")
        assert frame.region.width <= 76


@pytest.mark.asyncio
async def test_account_picker_fits_narrow():
    from whooing_tui.screens.account_picker import AccountPickerScreen
    from whooing_tui.screens.entries import EntriesScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        await app.push_screen(AccountPickerScreen(app.session, side="left"))
        await pilot.pause()
        frame = app.screen.query_one("#picker-frame")
        assert frame.region.width <= 40
        assert frame.region.width >= 30


@pytest.mark.asyncio
async def test_tags_picker_fits_narrow():
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.tags_picker import TagsPickerScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        await app.push_screen(TagsPickerScreen(item="", memo="", existing={}))
        await pilot.pause()
        frame = app.screen.query_one("#tagpick-frame")
        assert frame.region.width <= 40


@pytest.mark.asyncio
async def test_reports_menu_fits_narrow():
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.reports import ReportsMenuScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        await app.push_screen(ReportsMenuScreen())
        await pilot.pause()
        frame = app.screen.query_one("#reports-menu-frame")
        assert frame.region.width <= 40


# ---- Phase 2: EntriesScreen DataTable 컴팩트 모드 ----------------------


@pytest.mark.asyncio
async def test_entries_screen_enters_compact_mode_on_narrow():
    """30-cell 터미널 → level 4 (모든 left/right/memo hidden) — 종래 회귀
    의 가장 좁은 케이스. CL #51125+ 에서 단계 모델로 확장됐지만 "전부
    숨김" 의 종래 의도 그대로.
    """
    from whooing_tui.screens.entries import EntriesScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(30, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 4
        assert es._compact is True  # 후방 호환 property — level >= 2.
        table = es.query_one("#entries-table", DataTable)
        cols = list(table.columns.values())
        # left(2), right(3), memo(5) 모두 width=0.
        assert cols[2].width == 0
        assert cols[3].width == 0
        assert cols[5].width == 0


@pytest.mark.asyncio
async def test_entries_screen_normal_mode_on_wide_terminal():
    """120-cell 터미널 → level 0, left=12 (CL #51051 기본값)."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 0
        assert es._compact is False
        table = es.query_one("#entries-table", DataTable)
        cols = list(table.columns.values())
        assert cols[2].width == 12  # left


class _ClientWithEntries(_Client):
    """Phase 3 가로 스크롤 테스트용 — 1건 거래 + 6 컬럼 모두 채움."""

    def __init__(self) -> None:
        super().__init__()
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [{"account_id": "x20", "title": "식비"}],
        }
        self._entries = [{
            "entry_id": "e1", "entry_date": "20260510",
            "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스", "memo": "오후",
        }]


@pytest.mark.asyncio
async def test_level4_skips_hidden_left_right_on_right_arrow():
    """CL #51121 / CL #51125+: level 4 (width<35) — left/right 모두 hidden,
    → 가 둘 다 skip 하고 date(0) → money(1) → item(4) 로."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(30, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 4
        es.action_next_column()  # 활성화 col 0
        await pilot.pause()
        assert es._active_col == 0
        es.action_next_column()  # money col 1
        await pilot.pause()
        assert es._active_col == 1
        es.action_next_column()  # left/right hidden → item col 4
        await pilot.pause()
        assert es._active_col == 4


@pytest.mark.asyncio
async def test_level4_skips_hidden_columns_on_left_arrow():
    """level 4 — item(4) ← 면 right/left 둘 다 skip → money(1) → date(0)."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(30, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es._column_active = True
        es._active_col = 4
        es.action_prev_column()
        await pilot.pause()
        assert es._active_col == 1
        es.action_prev_column()
        await pilot.pause()
        assert es._active_col == 0
        es.action_prev_column()
        await pilot.pause()
        assert es._active_col == 0


@pytest.mark.asyncio
async def test_normal_mode_does_not_skip_columns():
    """정상 모드 (120-cell) 에서는 hidden skip 동작 안 함 — 6 컬럼 모두 순회."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact is False
        # 활성화 + 차례로 →.
        es.action_next_column()  # 활성화 col 0
        await pilot.pause()
        for expected in range(1, 6):
            es.action_next_column()
            await pilot.pause()
            assert es._active_col == expected, f"expected col {expected}"


@pytest.mark.asyncio
async def test_active_col_scrolls_into_view_on_navigation():
    """level 4 (width 30) — ←/→ 후 활성 컬럼이 화면 안에 들어오도록 scroll."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(30, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        es.action_next_column()  # 활성화 col 0
        es.action_next_column()  # money col 1
        es.action_next_column()  # item col 4 (level 4 — left/right hidden skip)
        await pilot.pause()
        assert es._active_col == 4
        coord = Coordinate(table.cursor_row, 4)
        try:
            cell_region = table._get_cell_region(coord)
        except Exception:
            pytest.skip("DataTable internal API _get_cell_region 미공개")
        viewport = table.scrollable_content_region.width
        assert cell_region.x >= int(table.scroll_x)
        assert cell_region.x <= int(table.scroll_x) + viewport


@pytest.mark.asyncio
async def test_entries_screen_threshold_boundary():
    """`_NARROW_THRESHOLD` (legacy alias = thresholds[1] = 60) 이상이면
    `_compact` False (level 0 또는 1), 미만이면 True (level >= 2). 기존
    호출자 / 단순 boolean 검사 회귀 보호.
    """
    from whooing_tui.screens.entries import EntriesScreen

    threshold = EntriesScreen._NARROW_THRESHOLD
    # 미만 → level 2+ → _compact True.
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(threshold - 1, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        assert app.screen._compact is True
        assert app.screen._compact_level >= 2

    # 같거나 초과 → level 0 또는 1 → _compact False.
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(threshold, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        assert app.screen._compact is False
        assert app.screen._compact_level < 2


# ---- CL #51125+ 단계별 4-tier 동작 검증 --------------------------------


@pytest.mark.asyncio
async def test_level1_hides_only_memo():
    """폭 70 (60 < w < 80) → level 1: memo 만 hidden, left=12 / right auto / L,R 헤더."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(70, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 1
        cols = list(es.query_one("#entries-table", DataTable).columns.values())
        # left=12 (정상), right auto (width=0+auto), memo strict hidden.
        assert cols[2].width == 12
        assert cols[5].width == 0 and cols[5].auto_width is False
        # 헤더는 종래대로 'left'/'right' (level 1 에선 약어 안 함).
        assert "left" in str(cols[2].label).lower()
        assert "right" in str(cols[3].label).lower()


@pytest.mark.asyncio
async def test_level2_abbreviates_left_right_with_L_R_headers():
    """폭 50 (45 < w < 60) → level 2: memo hidden + L/R 헤더 + 셀 약어."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(50, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 2
        table = es.query_one("#entries-table", DataTable)
        cols = list(table.columns.values())
        # 컬럼 폭: left=4, right=4 (한글 2글자), memo hidden.
        assert cols[2].width == 4
        assert cols[3].width == 4
        assert cols[5].width == 0 and cols[5].auto_width is False
        # 헤더: L / R 대문자.
        assert str(cols[2].label) == "L"
        assert str(cols[3].label) == "R"


@pytest.mark.asyncio
async def test_level3_hides_right_keeps_left_abbreviated():
    """폭 40 (35 < w < 45) → level 3: right 도 hidden, left 만 약어 visible."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 3
        cols = list(es.query_one("#entries-table", DataTable).columns.values())
        assert cols[2].width == 4   # left 약어
        assert cols[3].width == 0   # right hidden
        assert cols[3].auto_width is False
        assert cols[5].width == 0   # memo hidden
        assert str(cols[2].label) == "L"


@pytest.mark.asyncio
async def test_level3_skips_only_right_not_left():
    """level 3 — left 는 visible, right 만 skip. 네비: date→money→left→item."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 3
        es.action_next_column()  # col 0 (활성화)
        await pilot.pause()
        es.action_next_column()  # col 1 money
        await pilot.pause()
        es.action_next_column()  # col 2 left (visible)
        await pilot.pause()
        assert es._active_col == 2
        es.action_next_column()  # col 3 right hidden → skip → col 4 item
        await pilot.pause()
        assert es._active_col == 4


@pytest.mark.asyncio
async def test_left_cell_is_abbreviated_at_level2():
    """level 2 — left 셀 내용이 한글 2글자 약어. '식비' 그대로 (이미 2글자)."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(50, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        # row 0 의 left 셀 (col 2). _ClientWithEntries 의 entry l='식비'.
        cell = table.get_cell_at(Coordinate(0, 2))
        assert str(cell) == "식비"


@pytest.mark.asyncio
async def test_memo_cell_empty_at_level1_or_higher():
    """level 1+ 면 memo 셀 내용이 빈 문자열 — render 단계에서 숨김."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(70, 30)) as pilot:  # level 1
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 1
        table = es.query_one("#entries-table", DataTable)
        cell = table.get_cell_at(Coordinate(0, 5))  # memo
        assert str(cell) == ""


@pytest.mark.asyncio
async def test_level0_keeps_full_account_name():
    """level 0 (정상) — left 셀이 약어 X, 전체 계정명."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _ClientWithEntries()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(120, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact_level == 0
        table = es.query_one("#entries-table", DataTable)
        # _ClientWithEntries 의 left 계정 = '식비' (이미 2글자라 약어와 같음)
        # 약어 실행 여부보다 lvl 0 의 코드 path 확인이 핵심: memo 도 visible.
        cell_memo = table.get_cell_at(Coordinate(0, 5))
        assert str(cell_memo) == "오후"  # entry memo


# ---- _abbreviate_account_name unit tests ------------------------------


def test_abbreviate_keeps_first_two_korean_chars_short():
    """2자 이하 → 그대로, 3자 → 앞 2."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("식비") == "식비"
    assert EntriesScreen._abbreviate_account_name("교통비") == "교통"


def test_abbreviate_4char_korean_uses_first_and_third():
    """CL #51127+ 한국식 줄임말: 4글자 → 1번째 + 3번째 (스타벅스→스벅)."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("스타벅스") == "스벅"
    assert EntriesScreen._abbreviate_account_name("맥도날드") == "맥날"
    assert EntriesScreen._abbreviate_account_name("삼성전자") == "삼전"
    assert EntriesScreen._abbreviate_account_name("롯데리아") == "롯리"


def test_abbreviate_5plus_korean_falls_back_to_first_two():
    """5글자 이상 → 보수 fallback first 2 (현대자동차→현대)."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("현대자동차") == "현대"
    assert EntriesScreen._abbreviate_account_name("국민건강보험") == "국민"


def test_abbreviate_strips_company_prefix_then_applies_rule():
    """CL #51127+ 사용자 요청 핵심 케이스: '(주)스타벅스' → '스벅'.

    회사 prefix 를 strip 한 *남은 이름* 에 한국식 4글자 규칙 적용.
    """
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("(주)스타벅스") == "스벅"
    assert EntriesScreen._abbreviate_account_name("(유)맥도날드") == "맥날"
    assert EntriesScreen._abbreviate_account_name("주식회사 카카오") == "카카"
    assert EntriesScreen._abbreviate_account_name("주식회사카카오") == "카카"
    # prefix 없을 때도 결과는 같아야 — 사용자 직관과 일치.
    assert EntriesScreen._abbreviate_account_name("스타벅스") == "스벅"


def test_abbreviate_company_prefix_only_at_start_not_middle():
    """'(주)' 가 가운데 있으면 일반 괄호로 처리 — strip 후 일반 규칙."""
    from whooing_tui.screens.entries import EntriesScreen
    # "현금(주)" → 가운데 (주) 는 prefix 매칭 X → 일반 괄호 strip → "현금주" (3자) → "현금"
    assert EntriesScreen._abbreviate_account_name("현금(주)") == "현금"


def test_abbreviate_strips_general_brackets():
    """일반 괄호 (회사 prefix 가 아닌) → 글자만 strip 후 일반 규칙."""
    from whooing_tui.screens.entries import EntriesScreen
    # "[자산]현금" → "자산현금" (4자) → 한국식 1+3 = "자현".
    assert EntriesScreen._abbreviate_account_name("[자산]현금") == "자현"
    # "현금(주머니)" → "현금주머니" (5자) → fallback first 2 = "현금".
    assert EntriesScreen._abbreviate_account_name("현금(주머니)") == "현금"
    # "{메모}식비" → "메모식비" (4자) → 1+3 = "메식".
    assert EntriesScreen._abbreviate_account_name("{메모}식비") == "메식"


def test_abbreviate_korean_quotes_treated_as_brackets():
    """「」 『』 도 괄호류로 strip — 사용자 답변에서 명시."""
    from whooing_tui.screens.entries import EntriesScreen
    # "「인용」현금" → "인용현금" (4자) → 1+3 = "인현".
    assert EntriesScreen._abbreviate_account_name("「인용」현금") == "인현"
    # "『책』서비스" → "책서비스" (4자) → 1+3 = "책비".
    assert EntriesScreen._abbreviate_account_name("『책』서비스") == "책비"


def test_abbreviate_english_uses_simple_first_two():
    """영문 첫 글자 → 한국식 규칙 부적용 (의미 단위 가정 안 됨), 단순 [:2]."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("Starbucks") == "St"
    assert EntriesScreen._abbreviate_account_name("McDonald") == "Mc"
    assert EntriesScreen._abbreviate_account_name("BC카드") == "BC"
    assert EntriesScreen._abbreviate_account_name("a") == "a"
    assert EntriesScreen._abbreviate_account_name("") == ""
    assert EntriesScreen._abbreviate_account_name(None) == ""  # type: ignore[arg-type]


def test_abbreviate_mixed_first_char_dictates_rule():
    """첫 글자가 한글이면 한국식, 영문이면 단순 — mixed 케이스."""
    from whooing_tui.screens.entries import EntriesScreen
    # 첫 글자 영문 — 단순 [:2].
    assert EntriesScreen._abbreviate_account_name("T맵 택시") == "T맵"
    # 첫 글자 한글 — 한국식, 4자 → 1+3.
    # "한국T맵" (4자) → "한T".
    assert EntriesScreen._abbreviate_account_name("한국T맵") == "한T"


def test_abbreviate_strips_only_brackets_not_other_punctuation():
    """`,` `.` `-` 등 일반 punct 는 보존 (사용자 답변에서 추가 일반화 거부)."""
    from whooing_tui.screens.entries import EntriesScreen
    # "A.B" 영문 첫 글자 → [:2] = "A.".
    assert EntriesScreen._abbreviate_account_name("A.B") == "A."
    # "a-b-c" 영문 → [:2] = "a-".
    assert EntriesScreen._abbreviate_account_name("a-b-c") == "a-"


# ---- CL #51130+ 회사 suffix strip 휴리스틱 ------------------------------


def test_abbreviate_strips_korea_suffix_then_applies_4char_rule():
    """'스타벅스코리아' → '코리아' strip → '스타벅스' (4자) → 1+3 = '스벅'."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("스타벅스코리아") == "스벅"


def test_abbreviate_strips_group_suffix():
    """'삼성그룹' → '그룹' strip → '삼성' (2자) → 그대로."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("삼성그룹") == "삼성"


def test_abbreviate_strips_global_suffix():
    """'CJ글로벌' → '글로벌' strip → 'CJ' (2자, 영문) → 그대로."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("CJ글로벌") == "CJ"


def test_abbreviate_strips_holdings_suffix():
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("롯데홀딩스") == "롯데"


def test_abbreviate_strips_long_suffix_first():
    """긴 suffix 먼저 시도 — '인터내셔널' 이 '글로벌' 보다 우선이라 '코리아글로벌
    인터내셔널' 같은 케이스에서 부분 매칭 회피."""
    from whooing_tui.screens.entries import EntriesScreen
    # "ABC인터내셔널" → strip → "ABC" → 영문 [:2] = "AB".
    assert EntriesScreen._abbreviate_account_name("ABC인터내셔널") == "AB"


def test_abbreviate_keeps_suffix_when_strip_would_leave_too_short():
    """suffix 만 strip 하면 1자 이하로 남는 경우 — strip 안 함 (안전망)."""
    from whooing_tui.screens.entries import EntriesScreen
    # "X그룹" → "그룹" strip 하면 "X" 1자 → strip 안 함. 원본 유지.
    # "X그룹" 영문 첫 글자 → 단순 [:2] = "X그".
    assert EntriesScreen._abbreviate_account_name("X그룹") == "X그"


def test_abbreviate_does_not_strip_suffix_in_middle():
    """suffix 가 가운데 있으면 strip X (회사 식별자가 아닌 일반 단어)."""
    from whooing_tui.screens.entries import EntriesScreen
    # "그룹사물놀이" — '그룹' 이 시작에 있고 끝이 아니라 strip X. 5자 → first 2 = "그룹".
    assert EntriesScreen._abbreviate_account_name("그룹사물놀이") == "그룹"


def test_abbreviate_combines_prefix_and_suffix_strip():
    """prefix + suffix 둘 다 — '(주)스타벅스코리아' → '스벅'."""
    from whooing_tui.screens.entries import EntriesScreen
    assert EntriesScreen._abbreviate_account_name("(주)스타벅스코리아") == "스벅"


def test_abbreviate_corporation_suffix_5char():
    """'코퍼레이션' 같은 5글자 suffix 도 strip."""
    from whooing_tui.screens.entries import EntriesScreen
    # "삼성코퍼레이션" → '코퍼레이션' strip → "삼성" → 그대로.
    assert EntriesScreen._abbreviate_account_name("삼성코퍼레이션") == "삼성"
    # "현대자동차코퍼레이션" → strip → "현대자동차" (5자) → first 2 = "현대".
    assert EntriesScreen._abbreviate_account_name("현대자동차코퍼레이션") == "현대"
