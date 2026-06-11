"""AccountFlowScreen — 렌더러 + 분석 메뉴/fetch 흐름 (0.84.0, 로드맵 P2-B)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.account_flow import _render_flow


# ---- _render_flow 단위 -------------------------------------------------


def test_render_flow_aggregate_and_rows():
    payload = {
        "aggregate": {"in": 1010002, "out": 298933},
        "rows_type": "day",
        "rows": {
            "20260601": {"money": 12000},
            "20260602": {"money": 4500},
        },
    }
    out = _render_flow(payload)
    assert "1,010,002" in out      # aggregate 콤마.
    assert "12,000" in out         # rows money 콤마.
    assert "```" not in out        # raw JSON fallback 아님.


def test_render_flow_list_of_items():
    payload = [
        {"item": "스타벅스", "money": 45000},
        {"item": "투썸", "money": 12000},
    ]
    out = _render_flow(payload)
    assert "스타벅스" in out
    assert "45,000" in out


def test_render_flow_empty():
    assert "결과 없음" in _render_flow([])
    assert "결과 없음" in _render_flow({})


def test_render_flow_unknown_shape_falls_back_to_json():
    out = _render_flow({"weird": [1, 2, 3]})
    assert "```" in out
    assert "weird" in out


# ---- 통합 -------------------------------------------------------------


class _FakeClient:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"expenses": [{"account_id": "x20", "title": "식비"}]}
        self.calls: list[dict[str, Any]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def call_official_tool(self, name, args):
        self.calls.append(args)
        return {"aggregate": {"in": 100, "out": 50},
                "rows": {"20260601": {"money": 30}}}


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


@pytest.mark.asyncio
async def test_screen_fetches_items_of_account_id_first():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.account_flow import AccountFlowScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1", timeout=3.0,
        )
        app.push_screen(AccountFlowScreen(
            fake, app.session, account="expenses",
            account_id="x20", title="식비",
        ))
        await _pump_until(pilot, lambda: len(fake.calls) >= 1)
        first = fake.calls[0]
        assert first["type"] == "entries_items_of_account_id"
        assert first["account"] == "expenses"
        assert first["account_id"] == "x20"
        # 결과 패널에 렌더 반영.
        from textual.widgets import Static
        screen = app.screen
        body = str(screen.query_one("#af_result_body", Static).render())
        assert "100" in body or "요약" in body


@pytest.mark.asyncio
async def test_switching_analysis_refetches_with_new_type():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.account_flow import AccountFlowScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1", timeout=3.0,
        )
        screen = AccountFlowScreen(
            fake, app.session, account="expenses",
            account_id="x20", title="식비",
        )
        app.push_screen(screen)
        await _pump_until(pilot, lambda: len(fake.calls) >= 1)
        from textual.widgets import OptionList
        # 두 번째 분석(거래처별)로 이동.
        screen.query_one("#af_menu", OptionList).highlighted = 1
        await _pump_until(
            pilot,
            lambda: any(c["type"] == "entries_clients_of_account_id"
                        for c in fake.calls),
        )
        assert any(c["type"] == "entries_clients_of_account_id"
                   for c in fake.calls)


def test_menu_includes_open_account_flow():
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [it.action_id for m in menus for it in m.items]
    assert "open_account_flow" in flat
