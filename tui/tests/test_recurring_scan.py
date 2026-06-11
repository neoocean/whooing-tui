"""RecurringOmissionScreen + entries.py 의 "입력 → 반복 거래 누락 검사"
wiring 테스트.

거래 날짜는 '오늘' 기준 상대값으로 생성 — 시스템 날짜와 무관하게 결정적.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_core.dates import now_kst

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.recurring_scan import (
    RecurringOmissionScreen,
    RecurringScanRangeModal,
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    yield tmp_path


def _months_ago(n: int) -> str:
    """오늘에서 n 달 전, 일(day)은 min(오늘일, 28) 로 고정 → 항상 유효."""
    base = now_kst().date()
    day = min(base.day, 28)
    m = base.month - 1 - n
    y = base.year + m // 12
    mm = m % 12 + 1
    return f"{y:04d}{mm:02d}{day:02d}"


def _monthly_with_gap() -> tuple[list[dict[str, Any]], str]:
    """7회차 월간 시리즈 + 한 달(offset 4) 빠짐. (entries, 빠진날짜) 반환."""
    offsets = [1, 2, 3, 5, 6, 7, 8]  # offset 4 누락.
    entries = [
        {"entry_id": f"n{o}", "entry_date": _months_ago(o), "money": 13500,
         "l_account_id": "x22", "r_account_id": "x11", "item": "넷플릭스"}
        for o in offsets
    ]
    # 비반복 단발 거래.
    entries.append({
        "entry_id": "z", "entry_date": _months_ago(2), "money": 99999,
        "l_account_id": "x20", "r_account_id": "x11", "item": "일회성",
    })
    return entries, _months_ago(4)


class FakeClient:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x22", "title": "구독료"},
            ],
        }
        self._entries = entries
        self.list_entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self) -> list[dict[str, Any]]:
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date, *,
                           on_progress=None):
        self.list_entries_calls.append((section_id, start_date, end_date))
        if on_progress is not None:
            try:
                on_progress("fetch", start_date, end_date)
                on_progress("received", start_date, end_date,
                            count=len(self._entries))
                on_progress("done", start_date, end_date,
                            total=len(self._entries))
            except Exception:
                pass
        return list(self._entries)


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
    await _wait_for(lambda: app.screen.last_entry_count >= 1, timeout=2.0)
    return app.screen  # type: ignore[return-value]


async def _drive_to_overview(app, days: int = 365) -> RecurringOmissionScreen:
    es = app.screen
    es.action_scan_recurring()
    await _wait_for(
        lambda: isinstance(app.screen, RecurringScanRangeModal), timeout=3.0,
    )
    app.screen.dismiss(days)
    await _wait_for(
        lambda: isinstance(app.screen, RecurringOmissionScreen), timeout=5.0,
    )
    return app.screen  # type: ignore[return-value]


# ---- 메뉴 wiring (pure) ------------------------------------------------


def test_scan_recurring_in_input_menu():
    menus = EntriesScreen._build_menus()
    input_menu = next(m for m in menus if m.name == "입력")
    labels = [it.label for it in input_menu.items]
    assert any("반복 거래 누락 검사" in lab for lab in labels)
    ids = [it.action_id for it in input_menu.items]
    assert "scan_recurring" in ids


# ---- 통합: 검사 → overview --------------------------------------------


@pytest.mark.asyncio
async def test_scan_recurring_detects_gap():
    entries, missing_date = _monthly_with_gap()
    app = WhooingTuiApp(client=FakeClient(entries))
    async with app.run_test() as pilot:
        await _open_entries(app)
        overview = await _drive_to_overview(app)
        assert len(overview._series) == 1
        s = overview._series[0]
        assert s.cadence == "monthly"
        assert s.item == "넷플릭스"
        assert any(
            m.get("expected_date") == missing_date and m.get("kind") == "gap"
            for m in s.missing
        )
        # 후잉을 실제로 한 번 호출했다 (cache miss).
        assert len(app.screen._series) >= 1


@pytest.mark.asyncio
async def test_mark_handled_then_cache_hit():
    entries, _ = _monthly_with_gap()
    app = WhooingTuiApp(client=FakeClient(entries))
    async with app.run_test() as pilot:
        await _open_entries(app)
        overview = await _drive_to_overview(app)
        sid = overview._series[0].id
        overview.action_mark_handled()
        assert overview._series[0].status == "handled"
        # repo 에 반영됐는지 — open 에서 빠짐.
        from whooing_tui.recurring_scan_repo import RecurringScanRepository
        repo = RecurringScanRepository()
        opens = repo.load_open_series(
            section_id=overview._section_id,
            range_start=overview._range_start,
            range_end=overview._range_end,
        )
        assert all(o.id != sid for o in opens)


@pytest.mark.asyncio
async def test_clean_series_no_overview():
    # 누락 없는 깨끗한 월간 시리즈 → overview 안 뜸, 상태만.
    offsets = [1, 2, 3, 4, 5, 6]
    entries = [
        {"entry_id": f"c{o}", "entry_date": _months_ago(o), "money": 9900,
         "l_account_id": "x22", "r_account_id": "x11", "item": "정기구독"}
        for o in offsets
    ]
    app = WhooingTuiApp(client=FakeClient(entries))
    async with app.run_test() as pilot:
        es = await _open_entries(app)
        es.action_scan_recurring()
        await _wait_for(
            lambda: isinstance(app.screen, RecurringScanRangeModal), timeout=3.0,
        )
        app.screen.dismiss(365)
        # overview 안 뜨고 EntriesScreen 으로 복귀 + 상태에 '없음'.
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and "없음" in es.last_status,
            timeout=5.0,
        )
        assert isinstance(app.screen, EntriesScreen)
