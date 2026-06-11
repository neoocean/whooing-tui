"""ReportCustomsScreen — list / 신규 / 삭제 / BS·PL 전환 (0.84.0).

FakeClient.call_official_tool 로 report_customs-* 위임 args 를 capture.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp


class _FakeClient:
    def __init__(self, rows_bs=None, rows_pl=None) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"assets": [{"account_id": "x11", "title": "현금"}]}
        self._rows = {
            "report_bs": list(rows_bs or []),
            "report_pl": list(rows_pl or []),
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []

    async def call_official_tool(self, name, args):
        self.calls.append((name, args))
        if name == "report_customs-list":
            return {"rows": list(self._rows.get(args["report"], []))}
        if name == "report_customs-create":
            self._rows[args["report"]].append({
                "id": str(len(self._rows[args["report"]]) + 1),
                "title": args["title"], "plus": args["plus"],
                "minus": args["minus"], "addminus": args["addminus"],
            })
            return {"status": "done"}
        if name == "report_customs-delete":
            self._rows[args["report"]] = [
                r for r in self._rows[args["report"]]
                if str(r.get("id")) != args["custom_id"]
            ]
            return {"status": "done"}
        return {}


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


async def _open(app, pilot):
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.report_customs import ReportCustomsScreen
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1", timeout=3.0,
    )
    app.screen.action_open_report_customs()
    await _pump_until(pilot, lambda: isinstance(app.screen, ReportCustomsScreen))
    return app.screen


_BS_ROW = {"id": "12", "title": "현금성 자산",
           "plus": ["assets_x11", "assets_x12"], "minus": [],
           "addminus": "x"}


@pytest.mark.asyncio
async def test_lists_bs_rows():
    fake = _FakeClient(rows_bs=[_BS_ROW])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        screen = await _open(app, pilot)
        await _wait_for(lambda: len(screen._rows) == 1, timeout=2.0)
        from textual.widgets import DataTable
        assert screen.query_one("#rc_table", DataTable).row_count == 1
        # 기본 보고서는 report_bs.
        assert any(c[0] == "report_customs-list"
                   and c[1]["report"] == "report_bs" for c in fake.calls)


@pytest.mark.asyncio
async def test_toggle_switches_to_pl():
    fake = _FakeClient(rows_bs=[_BS_ROW], rows_pl=[])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        screen = await _open(app, pilot)
        await _wait_for(lambda: len(screen._rows) == 1, timeout=2.0)
        screen.action_toggle_report()
        assert screen._report == "report_pl"   # 즉시 전환.
        ok = await _pump_until(
            pilot,
            lambda: any(c[1].get("report") == "report_pl"
                        for c in fake.calls if c[0] == "report_customs-list"),
        )
        assert ok


@pytest.mark.asyncio
async def test_new_row_creates_via_official_tool():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from textual.widgets import Input
        from whooing_tui.screens.report_customs import _ReportCustomEditModal
        screen = await _open(app, pilot)
        screen.action_new_row()
        await _pump_until(pilot, lambda: isinstance(app.screen, _ReportCustomEditModal))
        modal = app.screen
        modal.query_one("#rc-title", Input).value = "현금성 자산"
        modal.query_one("#rc-plus", Input).value = "assets_x11, assets_x12"
        modal.action_save()
        await _pump_until(
            pilot,
            lambda: any(c[0] == "report_customs-create" for c in fake.calls),
        )
        create = next(c for c in fake.calls if c[0] == "report_customs-create")
        assert create[1]["title"] == "현금성 자산"
        assert create[1]["plus"] == ["assets_x11", "assets_x12"]
        assert create[1]["minus"] == []


@pytest.mark.asyncio
async def test_new_row_requires_title_and_refs():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.report_customs import _ReportCustomEditModal
        screen = await _open(app, pilot)
        screen.action_new_row()
        await _pump_until(pilot, lambda: isinstance(app.screen, _ReportCustomEditModal))
        app.screen.action_save()   # 빈 입력.
        await pilot.pause()
        assert not any(c[0] == "report_customs-create" for c in fake.calls)
        assert isinstance(app.screen, _ReportCustomEditModal)


def test_menu_includes_open_report_customs():
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [it.action_id for m in menus for it in m.items]
    assert "open_report_customs" in flat


def test_parse_refs_helper():
    from whooing_tui.screens.report_customs import _parse_refs
    assert _parse_refs("assets_x11, assets_x12 ,") == ["assets_x11", "assets_x12"]
    assert _parse_refs("") == []
