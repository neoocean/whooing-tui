"""DuplicateEvalScreen + entries.py 의 m 컨텍스트메뉴 wiring 통합 테스트.

CL #52815+ — 사용자 요청: 2건 이상 선택 후 m 메뉴에서 '중복인지 평가'.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import DataTable

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError
from whooing_tui.screens.dupe_eval import DuplicateEvalScreen
from whooing_tui.screens.entries import EntriesScreen


class FakeClient:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x21", "title": "교통비"},
            ],
        }
        # 두 개의 동일 거래 — 중복 시나리오.
        self._entries = entries if entries is not None else [
            {
                "entry_id": "e1", "entry_date": "20260510",
                "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
                "item": "스타벅스",
            },
            {
                "entry_id": "e2", "entry_date": "20260510",
                "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
                "item": "스타벅스",
            },
        ]
        self.delete_calls: list[dict[str, Any]] = []
        self.delete_error: ToolError | None = None

    async def list_sections(self) -> list[dict[str, Any]]:
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def delete_entry(self, *, section_id, entry_id) -> dict[str, Any]:
        if self.delete_error is not None:
            raise self.delete_error
        self.delete_calls.append({"section_id": section_id, "entry_id": entry_id})
        self._entries = [e for e in self._entries if e.get("entry_id") != entry_id]
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
        and app.session.section_id == "s1",
        timeout=3.0,
    )
    await _wait_for(
        lambda: app.screen.last_entry_count >= 2, timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_m_menu_does_not_show_eval_when_no_selection():
    """선택 0건 — 컨텍스트 메뉴에 '중복인지 평가' 가 없어야."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        es.action_show_context_menu()
        await pilot.pause()
        # 현재 screen 은 MenuPopup
        from whooing_tui.widgets.menubar import MenuPopup
        assert isinstance(app.screen, MenuPopup)
        labels = [it.label for it in app.screen.spec.items]
        assert not any("중복" in lab for lab in labels)
        app.screen.dismiss(None)


@pytest.mark.asyncio
async def test_m_menu_shows_eval_when_2plus_selected():
    """2건 선택 후 m — '중복인지 평가' 항목 노출."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        # e1, e2 직접 selection 에 추가.
        es._selected_entry_ids.add("e1")
        es._selected_entry_ids.add("e2")
        es.action_show_context_menu()
        await pilot.pause()
        from whooing_tui.widgets.menubar import MenuPopup
        assert isinstance(app.screen, MenuPopup)
        labels = [it.label for it in app.screen.spec.items]
        assert any("중복인지 평가" in lab for lab in labels)
        ids = [it.action_id for it in app.screen.spec.items]
        assert "evaluate_duplicates" in ids
        app.screen.dismiss(None)


@pytest.mark.asyncio
async def test_eval_screen_with_identical_entries_offers_dedup():
    """두 개의 동일 거래 → identical verdict, dedup 실행 시 1건 삭제."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        es._selected_entry_ids.add("e1")
        es._selected_entry_ids.add("e2")
        es.action_evaluate_duplicates()
        # worker 안에서 push_screen_wait — DuplicateEvalScreen 이 떠야.
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateEvalScreen),
            timeout=3.0,
        )
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, DuplicateEvalScreen)
        assert scr.last_verdict == "identical"
        # keep_suggestion = e1 (사전순 작은). dedup 실행 → e2 삭제.
        scr._dedup_kickoff()
        ok = await _wait_for(
            lambda: len(fake.delete_calls) == 1, timeout=3.0,
        )
        assert ok
        assert fake.delete_calls[0]["entry_id"] == "e2"


@pytest.mark.asyncio
async def test_eval_screen_with_different_entries_shows_not_duplicate():
    """서로 다른 거래 → different verdict, dedup 버튼은 숨김."""
    fake = FakeClient(entries=[
        {
            "entry_id": "a", "entry_date": "20260510",
            "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스",
        },
        {
            "entry_id": "b", "entry_date": "20260101",
            "money": 99999, "l_account_id": "x21", "r_account_id": "x11",
            "item": "버스",
        },
    ])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        es._selected_entry_ids.add("a")
        es._selected_entry_ids.add("b")
        es.action_evaluate_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateEvalScreen),
            timeout=3.0,
        )
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, DuplicateEvalScreen)
        assert scr.last_verdict == "different"
        # 사용자가 닫기 누르면 False dismiss → 변경 없음.
        scr.dismiss(False)
        await pilot.pause()
        assert fake.delete_calls == []


@pytest.mark.asyncio
async def test_eval_action_aborts_when_less_than_two_selected():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        # 0건 — 에러 status.
        es.action_evaluate_duplicates()
        await pilot.pause()
        assert "2건 이상" in es.last_status
        # 1건도 거부.
        es._selected_entry_ids.add("e1")
        es.action_evaluate_duplicates()
        await pilot.pause()
        assert "2건 이상" in es.last_status


@pytest.mark.asyncio
async def test_eval_screen_dedup_failure_reports_error():
    """삭제 중 일부 실패 — status 에 실패 갯수 노출."""
    fake = FakeClient()
    fake.delete_error = ToolError("USER_INPUT", "권한 없음")
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        es._selected_entry_ids.add("e1")
        es._selected_entry_ids.add("e2")
        es.action_evaluate_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateEvalScreen),
            timeout=3.0,
        )
        scr = app.screen
        assert isinstance(scr, DuplicateEvalScreen)
        scr._dedup_kickoff()
        ok = await _wait_for(
            lambda: "실패" in scr.last_status, timeout=3.0,
        )
        assert ok
