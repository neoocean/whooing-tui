"""거래 입력 자동완성 (0.84.0, 로드맵 P1-A).

서버 최근 아이템(entries_latest_items)을 prefetch → EntryEditDialog 의
item Input 에 inline 자동완성(SuggestFromList) 으로 주입.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import _extract_item_strings


# ---- _extract_item_strings 단위 (shape 관대) ---------------------------


def test_extract_from_list_of_dicts_dedup():
    payload = {"rows": [{"item": "스타벅스"}, {"item": "스타벅스"}, {"item": "버스"}]}
    assert _extract_item_strings(payload) == ["스타벅스", "버스"]


def test_extract_from_list_of_strings():
    assert _extract_item_strings(["커피", "커피", "점심"]) == ["커피", "점심"]


def test_extract_from_results_key_with_title():
    payload = {"results": [{"title": "택시"}, {"name": "지하철"}]}
    assert _extract_item_strings(payload) == ["택시", "지하철"]


def test_extract_handles_empty_and_garbage():
    assert _extract_item_strings(None) == []
    assert _extract_item_strings({}) == []
    assert _extract_item_strings([{"item": ""}, {"item": "  "}]) == []


# ---- 통합: prefetch → 새 거래 dialog 가 suggester 보유 ------------------


class _FakeClient:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "expenses": [{"account_id": "x20", "title": "식비"}],
            "assets": [{"account_id": "x11", "title": "현금"}],
        }
        self.official_calls: list[dict[str, Any]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def call_official_tool(self, name, args):
        self.official_calls.append(args)
        if args.get("type") == "entries_latest_items":
            return {"rows": [{"item": "스타벅스"}, {"item": "버스"},
                             {"item": "GS25"}]}
        return {}


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def _pump_until(pilot, predicate, *, steps=120):
    for _ in range(steps):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


def _item_input(screen):
    """f-item Input 이 mount 됐으면 반환, 아니면 None (compose 타이밍)."""
    from textual.widgets import Input
    try:
        return screen.query_one("#f-item", Input)
    except Exception:
        return None


@pytest.mark.asyncio
async def test_entries_prefetches_latest_items():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1", timeout=3.0,
        )
        es = app.screen
        ok = await _pump_until(
            pilot, lambda: es._item_suggestions == ["스타벅스", "버스", "GS25"],
        )
        assert ok
        assert any(c.get("type") == "entries_latest_items"
                   for c in fake.official_calls)


@pytest.mark.asyncio
async def test_new_entry_dialog_has_item_suggester():
    from textual.suggester import SuggestFromList
    from textual.widgets import Input
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.edit_entry import EntryEditDialog
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1", timeout=3.0,
        )
        es = app.screen
        await _pump_until(pilot, lambda: len(es._item_suggestions) == 3)
        es.action_new_entry()
        await _pump_until(pilot, lambda: isinstance(app.screen, EntryEditDialog))
        await _pump_until(pilot, lambda: _item_input(app.screen) is not None)
        item_input = _item_input(app.screen)
        assert isinstance(item_input.suggester, SuggestFromList)
        # 자동완성이 실제로 매칭하는지 (대소문자 무시 prefix).
        suggestion = await item_input.suggester.get_suggestion("스타")
        assert suggestion == "스타벅스"


@pytest.mark.asyncio
async def test_dialog_without_suggestions_has_no_suggester():
    """주입 없으면 suggester=None — 회귀 안전 (기존 동작 유지)."""
    from whooing_tui.widgets.input_modal import InputModal  # noqa: F401
    from textual.widgets import Input
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.edit_entry import EntryEditDialog
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=3.0,
        )
        # suggestions 없이 직접 push.
        app.push_screen(EntryEditDialog(app.session, existing={}))
        await _pump_until(pilot, lambda: isinstance(app.screen, EntryEditDialog))
        await _pump_until(pilot, lambda: _item_input(app.screen) is not None)
        assert _item_input(app.screen).suggester is None
