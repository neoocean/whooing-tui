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
