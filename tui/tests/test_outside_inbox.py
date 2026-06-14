"""OutsideInboxScreen — 조회 렌더 + 확정/삭제/비우기 worker 검증.

실제 HTTP 대신 fake OutsideClient 를 주입. worker 메서드를 직접 호출해
(모달 UI 흐름과 분리) 핵심 mutation 로직을 검증한다.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static

from whooing_tui.screens.outside_inbox import OutsideInboxScreen
from whooing_tui.state import SessionState


class FakeOutside:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)
        self.confirmed: list[tuple[list, list]] = []
        self.deleted: list[list[str]] = []
        self.emptied = False

    async def list_all(self, section_id: str, **kw) -> list[dict[str, Any]]:
        return list(self._rows)

    async def confirm(self, section_id, entries, del_ids):
        self.confirmed.append((entries, del_ids))
        return {}

    async def delete(self, section_id, del_ids):
        self.deleted.append(del_ids)
        return {}

    async def empty(self, section_id):
        self.emptied = True
        return {}


def _row(out_id="100", money=21500, r="liabilities_x80", **kw):
    base = {
        "out_id": out_id, "entry_date": "20260519", "money": money,
        "right": "하나카드(2*9*)", "r3": "쿠팡", "detail": "쿠팡[쿠페이]", "r": r,
    }
    base.update(kw)
    return base


class _Host(App):
    def __init__(self, screen: OutsideInboxScreen) -> None:
        super().__init__()
        self._scr = screen

    def compose(self) -> ComposeResult:
        yield Static("base")

    def on_mount(self) -> None:
        self.push_screen(self._scr)


def _screen(fake: FakeOutside) -> OutsideInboxScreen:
    return OutsideInboxScreen(
        fake, section_id="s9046", session=SessionState(section_id="s9046"),
    )


async def _settle(pilot, n: int = 6) -> None:
    for _ in range(n):
        await pilot.pause()


async def test_load_renders_rows():
    fake = FakeOutside([_row("100"), _row("101", money=4990)])
    scr = _screen(fake)
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        table = scr.query_one("#out-table", DataTable)
        assert table.row_count == 2
        assert [r["out_id"] for r in scr._rows] == ["100", "101"]


async def test_empty_inbox_status():
    fake = FakeOutside([])
    scr = _screen(fake)
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        assert "비어 있습니다" in scr.last_status


async def test_delete_worker_removes_row():
    fake = FakeOutside([_row("100"), _row("101")])
    scr = _screen(fake)
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        scr._delete_worker(scr._rows[0])
        await _settle(pilot)
        assert fake.deleted == [["100"]]
        assert [r["out_id"] for r in scr._rows] == ["101"]
        assert scr._dirty is True
        assert scr.query_one("#out-table", DataTable).row_count == 1


async def test_confirm_worker_creates_and_removes():
    fake = FakeOutside([_row("100")])
    scr = _screen(fake)
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        scr._confirm_worker(scr._rows[0], "x50", "expenses")
        await _settle(pilot)
        assert len(fake.confirmed) == 1
        entries, del_ids = fake.confirmed[0]
        assert del_ids == ["100"]
        assert entries[0]["l_account_id"] == "x50"
        assert entries[0]["l_account"] == "expenses"
        assert entries[0]["r_account_id"] == "x80"
        assert scr._rows == []
        assert scr._dirty is True


async def test_empty_worker_clears_all():
    fake = FakeOutside([_row("100"), _row("101")])
    scr = _screen(fake)
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        scr._empty_worker()
        await _settle(pilot)
        assert fake.emptied is True
        assert scr._rows == []
        assert scr.query_one("#out-table", DataTable).row_count == 0


async def test_confirm_no_selection_sets_status():
    fake = FakeOutside([])
    scr = _screen(fake)
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        scr.action_confirm()
        await pilot.pause()
        assert "선택된 항목이 없습니다" in scr.last_status
