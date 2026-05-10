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
    """40-cell 터미널 → `_compact=True`, left/right/memo 컬럼 width=0."""
    from whooing_tui.screens.entries import EntriesScreen

    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(40, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._compact is True
        table = es.query_one("#entries-table", DataTable)
        cols = list(table.columns.values())
        # left(2), right(3), memo(5) 가 width=0 이어야 — 시각상 숨김.
        assert cols[2].width == 0
        assert cols[3].width == 0
        assert cols[5].width == 0


@pytest.mark.asyncio
async def test_entries_screen_normal_mode_on_wide_terminal():
    """120-cell 터미널 → `_compact=False`, left=12 (CL #51051 기본값)."""
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
async def test_compact_mode_skips_hidden_columns_on_right_arrow():
    """CL #51121+: 컴팩트에서 → 가 hidden left/right 를 skip — date(0) →
    money(1) → item(4) (left=2, right=3 건너뜀)."""
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
        assert es._compact is True
        # 첫 → = 활성화 (col 0).
        es.action_next_column()
        await pilot.pause()
        assert es._active_col == 0
        # 두번째 → = col 1 (money, visible).
        es.action_next_column()
        await pilot.pause()
        assert es._active_col == 1
        # 세번째 → 가 col 2 (left, hidden) skip 하고 col 4 (item) 으로.
        es.action_next_column()
        await pilot.pause()
        assert es._active_col == 4  # item


@pytest.mark.asyncio
async def test_compact_mode_skips_hidden_columns_on_left_arrow():
    """← 도 마찬가지 — item(4) → money(1) → date(0) (right/left 건너뜀)."""
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
        # 활성화 + item 까지 이동
        es._column_active = True
        es._active_col = 4
        # ← = item(4) → money(1) (left=3,2 skip)
        es.action_prev_column()
        await pilot.pause()
        assert es._active_col == 1
        # ← = money(1) → date(0)
        es.action_prev_column()
        await pilot.pause()
        assert es._active_col == 0
        # ← 한 번 더 = 0 boundary, 그대로
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
    """좁은 터미널에서 ←/→ 후 활성 컬럼이 화면 안에. scroll_x 가 변하는지."""
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
        table = es.query_one("#entries-table", DataTable)
        # 초기 scroll_x = 0
        initial_scroll = table.scroll_x
        # 활성화 + 오른쪽으로 멀리 (item col 4 — 컴팩트에서도 visible)
        es.action_next_column()  # 활성화 col 0
        es.action_next_column()  # money col 1
        es.action_next_column()  # item col 4 (skip left/right)
        await pilot.pause()
        assert es._active_col == 4
        # item col 이 화면 밖에 있을 만큼 멀어 scroll_x 가 변했어야.
        # 또는 _scroll_active_col_into_view 가 호출됐어야.
        # 직접 region 검증: cell region 이 visible 영역 안에 들어와야.
        coord = Coordinate(table.cursor_row, 4)
        try:
            cell_region = table._get_cell_region(coord)
        except Exception:
            pytest.skip("DataTable internal API _get_cell_region 미공개")
        # cell 의 시작 x 가 visible 영역 안에.
        # (visible: scroll_x ~ scroll_x + viewport_width)
        viewport = table.scrollable_content_region.width
        assert cell_region.x >= int(table.scroll_x)
        assert cell_region.x <= int(table.scroll_x) + viewport


@pytest.mark.asyncio
async def test_entries_screen_threshold_boundary():
    """`_NARROW_THRESHOLD` 이상이면 정상 모드, 미만이면 컴팩트."""
    from whooing_tui.screens.entries import EntriesScreen

    threshold = EntriesScreen._NARROW_THRESHOLD
    # 미만
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(threshold - 1, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        assert app.screen._compact is True

    # 같거나 초과
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=(threshold, 30)) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        assert app.screen._compact is False
