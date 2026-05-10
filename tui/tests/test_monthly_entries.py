"""MonthlyEntriesScreen — list / 신규 / 삭제 흐름 회귀.

CL #51152+. 후잉 endpoint 가 추정 — 본 테스트는 client 호출 형식과 화면
상태만 검증 (실 후잉 호출 X, FakeClient).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError


class _FakeClient:
    def __init__(
        self,
        monthly_rows: list[dict[str, Any]] | None = None,
        list_error: ToolError | None = None,
    ) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "expenses": [{"account_id": "x20", "title": "식비"}],
            "assets": [{"account_id": "x11", "title": "현금"}],
        }
        self._monthly = monthly_rows or []
        self.list_error = list_error
        self.list_calls: list[str] = []
        self.create_calls: list[dict[str, Any]] = []
        self.delete_calls: list[tuple[str, str]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def list_monthly(self, *, section_id):
        self.list_calls.append(section_id)
        if self.list_error:
            raise self.list_error
        return list(self._monthly)

    async def create_monthly(self, **kwargs):
        self.create_calls.append(kwargs)
        new = {"id": f"m{len(self.create_calls)}", **kwargs}
        self._monthly.append(new)
        return new

    async def delete_monthly(self, *, section_id, monthly_id):
        self.delete_calls.append((section_id, monthly_id))
        self._monthly = [
            r for r in self._monthly
            if str(r.get("id") or r.get("monthly_id")) != monthly_id
        ]
        return {"status": "ok"}


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_screen_lists_monthly_rows():
    rows = [
        {"id": "m1", "target_day": 25, "money": 50000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "월세"},
        {"id": "m2", "target_day": 5, "money": 30000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "통신비"},
    ]
    fake = _FakeClient(monthly_rows=rows)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.monthly_entries import MonthlyEntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es = app.screen
        es.action_open_monthly()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, MonthlyEntriesScreen),
            timeout=2.0,
        )
        screen: MonthlyEntriesScreen = app.screen  # type: ignore[assignment]
        await _wait_for(lambda: len(screen._rows) == 2, timeout=2.0)
        assert fake.list_calls == ["s1"]
        from textual.widgets import DataTable
        table = screen.query_one("#m_table", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_screen_shows_endpoint_error_status():
    """endpoint mismatch (ToolError) → status 에 디버깅 안내."""
    fake = _FakeClient(list_error=ToolError("ENDPOINT", "404 Not Found"))
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.monthly_entries import MonthlyEntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es = app.screen
        es.action_open_monthly()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, MonthlyEntriesScreen),
            timeout=2.0,
        )
        screen = app.screen
        await _wait_for(
            lambda: "endpoint" in screen.last_status.lower()
            or "404" in screen.last_status,
            timeout=2.0,
        )
        assert "endpoint" in screen.last_status.lower() or "404" in screen.last_status


def test_menu_includes_open_monthly():
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "open_monthly" for _, action in flat)


def test_client_monthly_path_helpers():
    """endpoint path helper 가 추정 spec 대로."""
    from whooing_tui.client import WhooingClient
    assert WhooingClient._monthly_collection_path() == "/monthly.json"
    assert WhooingClient._monthly_path("m42") == "/monthly/m42.json"
