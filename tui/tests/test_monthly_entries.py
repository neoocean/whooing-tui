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


async def _pump_until(pilot, predicate, *, steps=80):
    """`pilot.pause()` 로 메시지 루프를 돌리며 predicate 충족까지 대기.

    worker 의 `push_screen_wait` 같이 메시지 펌프가 필요한 전이는
    asyncio.sleep 만으로는 안정적이지 않아 pilot.pause 로 펌프한다.
    """
    for _ in range(steps):
        if predicate():
            return True
        await pilot.pause()
    return predicate()


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


@pytest.mark.asyncio
async def test_new_entry_uses_account_picker_buttons():
    """CL #56830+ (C13): 신규 등록 modal 이 raw account_id 입력 대신
    AccountPicker 버튼을 통해 l/r account_id + type 을 모은다."""
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from textual.widgets import Input
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.monthly_entries import (
            MonthlyEntriesScreen, _MonthlyEditModal,
        )
        from whooing_tui.screens.edit_entry import _AccountButton

        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        app.screen.action_open_monthly()
        await _pump_until(
            pilot, lambda: isinstance(app.screen, MonthlyEntriesScreen),
        )
        app.screen.action_new_entry()
        await _pump_until(
            pilot, lambda: isinstance(app.screen, _MonthlyEditModal),
        )
        modal = app.screen
        modal.query_one("#me-day", Input).value = "25"
        modal.query_one("#me-money", Input).value = "50000"
        modal.query_one("#me-item", Input).value = "월세"
        # picker 트리 탐색 대신 버튼 API 를 직접 호출 — picker → 버튼 갱신
        # 의 콜백과 동일한 상태가 된다.
        modal.query_one("#me-left", _AccountButton).set_account(
            "x20", "식비", "expenses",
        )
        modal.query_one("#me-right", _AccountButton).set_account(
            "x11", "현금", "assets",
        )
        modal.action_save()
        await _pump_until(pilot, lambda: len(fake.create_calls) == 1)
        call = fake.create_calls[0]
        assert call["l_account_id"] == "x20"
        assert call["l_account"] == "expenses"
        assert call["r_account_id"] == "x11"
        assert call["r_account"] == "assets"
        assert call["target_day"] == 25
        assert call["money"] == 50000
        assert call["item"] == "월세"


@pytest.mark.asyncio
async def test_new_entry_requires_both_accounts():
    """계정 미선택 상태로 저장 시 create 호출이 일어나지 않는다."""
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from textual.widgets import Input
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.monthly_entries import (
            MonthlyEntriesScreen, _MonthlyEditModal,
        )

        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        app.screen.action_open_monthly()
        await _pump_until(
            pilot, lambda: isinstance(app.screen, MonthlyEntriesScreen),
        )
        app.screen.action_new_entry()
        await _pump_until(
            pilot, lambda: isinstance(app.screen, _MonthlyEditModal),
        )
        modal = app.screen
        modal.query_one("#me-day", Input).value = "25"
        modal.query_one("#me-money", Input).value = "50000"
        # 계정 버튼 미선택 → save noop.
        modal.action_save()
        await pilot.pause()
        assert fake.create_calls == []
        assert isinstance(app.screen, _MonthlyEditModal)


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
