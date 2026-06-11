"""FrequentItemsScreen — list / 신규 / 삭제 / 사용(Enter) 흐름 (0.84.0).

FakeClient 로 client 호출 형식 + 화면 상태 검증 (실 후잉 호출 X).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp


class _FakeClient:
    def __init__(self, frequent: list[dict[str, Any]] | None = None) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "expenses": [{"account_id": "x20", "title": "식비"}],
            "assets": [{"account_id": "x11", "title": "현금"}],
        }
        self._frequent = frequent or []
        self.create_calls: list[dict[str, Any]] = []
        self.delete_calls: list[tuple[str, str, str]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def list_frequent(self, *, section_id):
        return list(self._frequent)

    async def create_frequent(self, **kwargs):
        self.create_calls.append(kwargs)
        self._frequent.append({"item_id": f"f{len(self.create_calls)}", **kwargs})
        return self._frequent[-1]

    async def delete_frequent(self, *, section_id, slot, item_id):
        self.delete_calls.append((section_id, slot, item_id))
        self._frequent = [
            r for r in self._frequent if str(r.get("item_id")) != item_id
        ]
        return {"status": "ok"}


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def _pump_until(pilot, predicate, *, steps=80):
    for _ in range(steps):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


def _rows():
    return [
        {"item_id": "f1", "slot": "slot1", "item": "커피", "money": 4500,
         "l_account": "expenses", "l_account_id": "x20",
         "r_account": "assets", "r_account_id": "x11"},
        {"item_id": "f2", "slot": "slot1", "item": "점심", "money": 9000,
         "l_account": "expenses", "l_account_id": "x20",
         "r_account": "assets", "r_account_id": "x11"},
    ]


async def _open(app, pilot):
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.frequent_entries import FrequentItemsScreen
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1", timeout=3.0,
    )
    app.screen.action_open_frequent()
    await _pump_until(pilot, lambda: isinstance(app.screen, FrequentItemsScreen))
    return app.screen


@pytest.mark.asyncio
async def test_screen_lists_frequent_rows():
    fake = _FakeClient(frequent=_rows())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        screen = await _open(app, pilot)
        await _wait_for(lambda: len(screen._rows) == 2, timeout=2.0)
        from textual.widgets import DataTable
        assert screen.query_one("#f_table", DataTable).row_count == 2


@pytest.mark.asyncio
async def test_use_item_dismisses_with_prefill_draft():
    """Enter → 선택 자주입력을 entry_id 없는 prefill dict 로 반환 →
    EntriesScreen 이 값 채운 새 거래 폼(EntryEditDialog)을 연다."""
    fake = _FakeClient(frequent=_rows())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.edit_entry import EntryEditDialog
        screen = await _open(app, pilot)
        await _wait_for(lambda: len(screen._rows) == 2, timeout=2.0)
        from textual.widgets import DataTable
        screen.query_one("#f_table", DataTable).move_cursor(row=0)
        screen.action_use_item()
        # 자주입력 → 새 거래 폼으로 전이.
        ok = await _pump_until(
            pilot, lambda: isinstance(app.screen, EntryEditDialog),
        )
        assert ok
        # prefill 값 (entry_id 없음 → 새 거래 모드).
        assert app.screen._is_edit is False
        assert app.screen._existing.get("item") == "커피"
        assert app.screen._existing.get("l_account_id") == "x20"


@pytest.mark.asyncio
async def test_new_item_creates_via_client():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from textual.widgets import Input
        from whooing_tui.screens.frequent_entries import _FrequentEditModal
        from whooing_tui.screens.edit_entry import _AccountButton
        screen = await _open(app, pilot)
        screen.action_new_item()
        await _pump_until(pilot, lambda: isinstance(app.screen, _FrequentEditModal))
        modal = app.screen
        modal.query_one("#fe-item", Input).value = "커피"
        modal.query_one("#fe-money", Input).value = "4500"
        modal.query_one("#fe-left", _AccountButton).set_account("x20", "식비", "expenses")
        modal.query_one("#fe-right", _AccountButton).set_account("x11", "현금", "assets")
        modal.action_save()
        await _pump_until(pilot, lambda: len(fake.create_calls) == 1)
        call = fake.create_calls[0]
        assert call["slot"] == "slot1"
        assert call["item"] == "커피"
        assert call["money"] == 4500
        assert call["l_account_id"] == "x20"


@pytest.mark.asyncio
async def test_new_item_requires_item_and_accounts():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from textual.widgets import Input
        from whooing_tui.screens.frequent_entries import _FrequentEditModal
        screen = await _open(app, pilot)
        screen.action_new_item()
        await _pump_until(pilot, lambda: isinstance(app.screen, _FrequentEditModal))
        modal = app.screen
        modal.query_one("#fe-money", Input).value = "4500"   # item/계정 미입력.
        modal.action_save()
        await pilot.pause()
        assert fake.create_calls == []
        assert isinstance(app.screen, _FrequentEditModal)


def test_menu_includes_open_frequent():
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "open_frequent" for _, action in flat)
