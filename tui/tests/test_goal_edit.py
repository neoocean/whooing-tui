"""GoalEditScreen — 장기목표 + 월별 자본 목표 편집 회귀.

CL #51154+. setter endpoint 추정 — client 호출 형식 + 화면 상태 검증.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError


class _FakeClient:
    def __init__(self):
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {}
        self._budget_goal = {"amount": 100000000, "target_date": "20301231"}
        self._goal = [
            {"target_month": "202612", "amount": 5000000},
            {"target_month": "202611", "amount": 4500000},
        ]
        self.set_bg_calls: list[dict[str, Any]] = []
        self.set_g_calls: list[dict[str, Any]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def get_budget_goal(self, *, section_id):
        return dict(self._budget_goal)

    async def get_goal(self, *, section_id, start_date=None, end_date=None):
        return {"rows": list(self._goal)}

    async def set_budget_goal(self, *, section_id, amount, target_date=None):
        self.set_bg_calls.append({
            "section_id": section_id, "amount": amount,
            "target_date": target_date,
        })
        return {"status": "ok"}

    async def set_goal(self, *, section_id, target_month, amount):
        self.set_g_calls.append({
            "section_id": section_id, "target_month": target_month,
            "amount": amount,
        })
        return {"status": "ok"}


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_screen_lists_budget_goal_initially():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.goal_edit import GoalEditScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es = app.screen
        es.action_open_goal_edit()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, GoalEditScreen),
            timeout=2.0,
        )
        screen: GoalEditScreen = app.screen  # type: ignore[assignment]
        await _wait_for(lambda: len(screen._rows) == 1, timeout=2.0)
        assert screen._mode == "budget_goal"
        assert screen._rows[0]["target_date"] == "20301231"


@pytest.mark.asyncio
async def test_toggle_to_goal_lists_monthly():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.goal_edit import GoalEditScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es = app.screen
        es.action_open_goal_edit()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, GoalEditScreen),
            timeout=2.0,
        )
        screen: GoalEditScreen = app.screen  # type: ignore[assignment]
        await _wait_for(lambda: screen._mode == "budget_goal", timeout=2.0)
        screen.action_toggle_mode()
        await _wait_for(
            lambda: screen._mode == "goal" and len(screen._rows) == 2,
            timeout=2.0,
        )
        months = sorted(r["target_month"] for r in screen._rows)
        assert months == ["202611", "202612"]


def test_menu_includes_open_goal_edit():
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "open_goal_edit" for _, action in flat)
