"""EntryEditDialog — 작은 화면에서 화면 밖으로 나가지 않고 스크롤되는지 검증.

회귀 방지: 종전엔 #dialog-frame 이 height:auto 라 폼(8행×3 + 버튼)이 ~35행
으로 커져 30행 미만 터미널에서 하단 Save/Cancel 이 화면 밖으로 잘렸다. 이제
VerticalScroll + max-height:95% 라 프레임이 화면 안에 들어가고 내부가 스크롤
된다(포커스 이동 시 자동 스크롤).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.containers import VerticalScroll

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.edit_entry import EntryEditDialog
from whooing_tui.screens.entries import EntriesScreen


async def _wait_for(pred, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


class FakeClient:
    def __init__(self):
        self.sections = [{"section_id": "s1", "title": "가계부"}]
        self.accounts = {
            "expenses": [{"account_id": "x50", "title": "식비", "type": "expenses"}],
            "assets": [{"account_id": "x11", "title": "현금", "type": "assets"}],
        }

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(50, 18), (40, 14), (98, 30)])
async def test_edit_dialog_fits_within_small_screen(size):
    app = WhooingTuiApp(client=FakeClient())  # type: ignore[arg-type]
    async with app.run_test(size=size) as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1"
            and bool(app.session.accounts_flat)
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_new_entry()
        await _wait_for(lambda: isinstance(app.screen, EntryEditDialog))
        await pilot.pause()
        dlg = app.screen

        frame = dlg.query_one("#dialog-frame")
        # 스크롤 컨테이너로 바뀌었는지 (off-screen 대신 스크롤).
        assert isinstance(frame, VerticalScroll)
        # 모든 핵심 컨트롤이 DOM 에 존재(접근 가능) — date~tags + Save/Cancel.
        for wid in ("#f-date", "#f-money", "#f-left", "#f-right", "#f-item",
                    "#f-memo", "#f-tags", "#btn-save", "#btn-cancel"):
            assert dlg.query_one(wid) is not None
        # 프레임이 화면 세로를 넘지 않는다(= 화면 밖 잘림 없음).
        assert frame.region.height <= app.size.height
        assert frame.region.bottom <= app.size.height
        assert frame.region.y >= 0

        # 버튼이 가로로 프레임을 넘지 않는다(좁은 폭에서 Cancel 잘림 회귀 방지).
        # 버튼이 스크롤로 가려져 있을 수 있어 먼저 보이게 한 뒤 검사.
        save = dlg.query_one("#btn-save")
        cancel = dlg.query_one("#btn-cancel")
        save.scroll_visible(animate=False)
        await pilot.pause()
        for btn in (save, cancel):
            assert btn.region.width > 0
            assert btn.region.x >= frame.region.x
            assert btn.region.right <= frame.region.right
            assert btn.region.right <= app.size.width
