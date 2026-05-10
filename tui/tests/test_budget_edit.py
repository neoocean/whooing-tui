"""BudgetEditScreen — list / 편집 / 삭제 흐름 회귀.

CL #51153+. setter endpoint 가 추정 — client 호출 형식 + 화면 상태 검증.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError


class _FakeClient:
    def __init__(self, budget_rows=None, error=None):
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "expenses": [{"account_id": "x20", "title": "식비"}],
            "income": [{"account_id": "x90", "title": "급여"}],
        }
        self._budget = budget_rows or {}
        self.error = error
        self.set_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def get_budget(self, *, section_id, account, start_date=None, end_date=None):
        if self.error:
            raise self.error
        return {"rows": self._budget.get(account, [])}

    async def set_budget(self, *, section_id, account, account_id, amount,
                         start_date=None, end_date=None):
        self.set_calls.append({
            "section_id": section_id, "account": account,
            "account_id": account_id, "amount": amount,
        })
        return {"status": "ok"}

    async def delete_budget(self, *, section_id, account, account_id):
        self.delete_calls.append({
            "section_id": section_id, "account": account,
            "account_id": account_id,
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
async def test_screen_lists_expenses_budget():
    rows = {"expenses": [
        {"account_id": "x20", "budget": 200000, "actual": 180000},
    ]}
    fake = _FakeClient(budget_rows=rows)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.budget_edit import BudgetEditScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es = app.screen
        es.action_open_budget_edit()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, BudgetEditScreen),
            timeout=2.0,
        )
        screen: BudgetEditScreen = app.screen  # type: ignore[assignment]
        await _wait_for(lambda: len(screen._rows) == 1, timeout=2.0)
        assert screen._account == "expenses"
        assert screen._rows[0]["account_id"] == "x20"


@pytest.mark.asyncio
async def test_toggle_account_switches_to_income():
    rows = {
        "expenses": [{"account_id": "x20", "budget": 200000}],
        "income": [{"account_id": "x90", "budget": 5000000}],
    }
    fake = _FakeClient(budget_rows=rows)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.budget_edit import BudgetEditScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es = app.screen
        es.action_open_budget_edit()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, BudgetEditScreen),
            timeout=2.0,
        )
        screen: BudgetEditScreen = app.screen  # type: ignore[assignment]
        await _wait_for(lambda: len(screen._rows) == 1, timeout=2.0)
        screen.action_toggle_account()
        await _wait_for(
            lambda: screen._account == "income"
            and screen._rows
            and screen._rows[0]["account_id"] == "x90",
            timeout=2.0,
        )
        assert screen._rows[0]["account_id"] == "x90"


@pytest.mark.asyncio
async def test_endpoint_error_status():
    fake = _FakeClient(error=ToolError("ENDPOINT", "404"))
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.budget_edit import BudgetEditScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        app.screen.action_open_budget_edit()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, BudgetEditScreen),
            timeout=2.0,
        )
        screen = app.screen
        await _wait_for(
            lambda: "404" in screen.last_status, timeout=2.0,
        )


def test_menu_includes_open_budget_edit():
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "open_budget_edit" for _, action in flat)
