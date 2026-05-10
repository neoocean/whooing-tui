"""AccountPickerScreen — Textual App.run_test() 통합.

EntryEditDialog 의 left/right 버튼 → AccountPickerScreen 으로 push,
사용자가 항목 선택 → dismiss((account_id, title, type_key)) 흐름을 검증.

CL #51076+: free-text account 입력은 사라지고 본 picker 가 유일한 진입점.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.account_picker import AccountPickerScreen
from whooing_tui.screens.edit_entry import EntryEditDialog, _AccountButton
from whooing_tui.screens.entries import EntriesScreen


class FakeClient:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x21", "title": "교통비"},
            ],
        }
        self._entries: list[dict[str, Any]] = [
            {
                "entry_id": "e1", "entry_date": "20260510",
                "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
                "item": "스타벅스",
            },
        ]

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def create_entry(self, **kwargs):
        return {"entry_id": "new-1", **kwargs}

    async def update_entry(self, **kwargs):
        return {**kwargs}

    async def delete_entry(self, **kwargs):
        return {}


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def _open_entries(app) -> EntriesScreen:
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1"
        and app.session.id_of("식비") == "x20",
        timeout=3.0,
    )
    await _wait_for(
        lambda: app.screen.last_entry_count >= 1, timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_picker_pushes_from_edit_dialog_and_updates_button():
    """edit dialog 의 left 버튼 클릭 → picker push → 항목 선택 →
    버튼 라벨이 새 항목으로 갱신."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)  # noqa: F841
        es.action_edit_entry()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)
        dialog = app.screen
        # left 버튼은 현재 식비 (x20)
        left_btn = dialog.query_one("#f-left", _AccountButton)
        assert left_btn.account_id == "x20"
        # 버튼 클릭 트리거 — picker push
        dialog._open_account_picker("f-left")
        await pilot.pause()
        assert isinstance(app.screen, AccountPickerScreen)
        # 사용자가 교통비 (x21) 선택한 것과 동일하게 dismiss
        app.screen.dismiss(("x21", "교통비", "expenses"))
        await pilot.pause()
        # dialog 가 다시 활성, 버튼 라벨 갱신됨
        assert isinstance(app.screen, EntryEditDialog)
        assert left_btn.account_id == "x21"
        assert left_btn.acc_title == "교통비"
        assert left_btn.type_key == "expenses"


@pytest.mark.asyncio
async def test_picker_cancel_keeps_button():
    """picker 에서 Esc — dismiss(None) — 버튼 라벨 유지."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)  # noqa: F841
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        right_btn = dialog.query_one("#f-right", _AccountButton)
        before = (right_btn.account_id, right_btn.acc_title)
        dialog._open_account_picker("f-right")
        await pilot.pause()
        assert isinstance(app.screen, AccountPickerScreen)
        app.screen.dismiss(None)
        await pilot.pause()
        # 버튼 그대로
        assert (right_btn.account_id, right_btn.acc_title) == before


@pytest.mark.asyncio
async def test_picker_lists_all_accounts_sorted_by_type():
    """OptionList 가 assets → expenses 순으로 정렬."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import OptionList

        picker = AccountPickerScreen(app.session, side="left")
        await app.push_screen(picker)
        await pilot.pause()
        opt = picker.query_one("#acc-list", OptionList)
        # 옵션 ID 순 — 첫 항목은 assets (x11), 그 후 expenses (x20, x21).
        ids = [opt.get_option_at_index(i).id for i in range(opt.option_count)]
        assert ids == ["x11", "x20", "x21"]
