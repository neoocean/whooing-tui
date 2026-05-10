"""EntriesScreen — Textual App.run_test() 통합 테스트.

FakeClient 에 list_entries 를 추가해 entries 화면을 띄운 뒤:
  - 첫 mount 시 list_entries 가 호출되고 DataTable 이 채워진다.
  - account_id 가 SessionState 의 양방향 인덱스로 title 로 변환된다.
  - 100-cap 의심 (단일 일자에 100건) 시 warn 클래스 + last_cap_warning.
  - 'q' 로 HomeScreen 복귀.
  - '+' / '-' 가 윈도우를 ±7일 변경 + 재로드.
  - ToolError 발생 시 error 클래스 + 행 0건.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import DataTable, Static

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError


class FakeClient:
    """HomeScreen 테스트 의 FakeClient 를 entries 까지 확장."""

    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
        accounts_by_section: dict[str, dict[str, Any]] | None = None,
        entries_by_section: dict[str, list[dict[str, Any]]] | None = None,
        entries_error: ToolError | None = None,
    ) -> None:
        # `sections or default` 패턴은 빈 리스트도 falsy 로 잡아 default 로
        # 덮어버린다 — `is None` 으로 분기해야 빈 sections 케이스 테스트가
        # 의미를 가진다.
        self.sections = (
            sections if sections is not None
            else [{"section_id": "s1", "title": "main"}]
        )
        self.accounts_by_section = accounts_by_section or {
            "s1": {
                "assets": [{"account_id": "x11", "title": "현금"}],
                "expenses": [
                    {"account_id": "x20", "title": "식비"},
                    {"account_id": "x21", "title": "교통비"},
                ],
            },
        }
        self.entries_by_section = entries_by_section or {}
        self.entries_error = entries_error
        self.list_entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self) -> list[dict[str, Any]]:
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        return self.accounts_by_section.get(section_id, {})

    async def list_entries(
        self, section_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        self.list_entries_calls.append((section_id, start_date, end_date))
        if self.entries_error is not None:
            raise self.entries_error
        return list(self.entries_by_section.get(section_id, []))


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _sample_entries() -> list[dict[str, Any]]:
    return [
        {
            "entry_id": "e1", "entry_date": "20260510",
            "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스", "memo": "오후",
        },
        {
            "entry_id": "e2", "entry_date": "20260509",
            "money": 5500, "l_account_id": "x21", "r_account_id": "x11",
            "item": "지하철", "memo": "",
        },
    ]


@pytest.mark.asyncio
async def test_entries_screen_loads_after_home_pushes():
    fake = FakeClient(entries_by_section={"s1": _sample_entries()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # HomeScreen 의 자동 활성화로 s1 이 활성, accounts 까지 로드 대기
        await _wait_for(
            lambda: app.session.section_id == "s1"
            and app.session.id_of("식비") == "x20",
            timeout=3.0,
        )
        # 'e' 로 EntriesScreen push
        await pilot.press("e")
        ok = await _wait_for(
            lambda: fake.list_entries_calls
            and len(fake.list_entries_calls) >= 1,
            timeout=3.0,
        )
        assert ok, f"calls={fake.list_entries_calls}"

        # 활성 화면이 EntriesScreen 인지
        from whooing_tui.screens.entries import EntriesScreen
        assert isinstance(app.screen, EntriesScreen)

        # DataTable 이 채워졌는지
        ok2 = await _wait_for(
            lambda: app.screen.query_one("#entries-table", DataTable).row_count == 2,
            timeout=2.0,
        )
        assert ok2

        # account_id 가 title 로 변환되어야 함 (식비, 현금)
        table = app.screen.query_one("#entries-table", DataTable)
        # row 0 = 가장 최근 (entry_date desc): e1 (20260510, 식비/현금)
        col_count = len(table.columns)
        row0 = [
            str(table.get_cell_at((0, c))) for c in range(col_count)
        ]
        assert "20260510" in row0
        assert "12,000" in row0
        assert "식비" in row0
        assert "현금" in row0
        assert "스타벅스" in row0

        # 100-cap 경고 없음
        assert app.screen.last_cap_warning is False
        assert "error" not in app.screen.query_one("#status", Static).classes
        assert "warn" not in app.screen.query_one("#status", Static).classes


@pytest.mark.asyncio
async def test_entries_screen_q_returns_home():
    fake = FakeClient(entries_by_section={"s1": _sample_entries()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1", timeout=3.0)
        await pilot.press("e")
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.home import HomeScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=2.0,
        )
        await pilot.press("q")
        ok = await _wait_for(
            lambda: isinstance(app.screen, HomeScreen), timeout=2.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_entries_screen_window_extend_triggers_reload():
    fake = FakeClient(entries_by_section={"s1": _sample_entries()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1", timeout=3.0)
        await pilot.press("e")
        await _wait_for(
            lambda: len(fake.list_entries_calls) >= 1, timeout=2.0,
        )
        first_window = fake.list_entries_calls[0]
        # +7일
        await pilot.press("plus")
        ok = await _wait_for(
            lambda: len(fake.list_entries_calls) >= 2, timeout=2.0,
        )
        assert ok
        # 두 번째 호출의 start_date 가 더 이른 날짜여야 함
        second = fake.list_entries_calls[-1]
        assert second[2] == first_window[2]  # end_date 동일 (오늘)
        assert int(second[1]) < int(first_window[1])  # start_date 가 더 이전


@pytest.mark.asyncio
async def test_entries_screen_100_cap_warning_for_single_date():
    """단일 일자에 100건 = cap 도달 의심 → warn 클래스 + last_cap_warning."""
    bulk = [
        {
            "entry_id": f"e{i}", "entry_date": "20260510",
            "money": 1000 + i, "l_account_id": "x20", "r_account_id": "x11",
            "item": f"항목{i}",
        }
        for i in range(100)
    ]
    fake = FakeClient(entries_by_section={"s1": bulk})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1", timeout=3.0)
        await pilot.press("e")
        ok = await _wait_for(
            lambda: getattr(app.screen, "last_cap_warning", False) is True,
            timeout=3.0,
        )
        assert ok
        bar = app.screen.query_one("#status", Static)
        assert "warn" in bar.classes
        assert "100-cap" in app.screen.last_status
        assert "20260510" in app.screen.last_status


@pytest.mark.asyncio
async def test_entries_screen_error_shows_status_error():
    fake = FakeClient(
        entries_error=ToolError("RATE_LIMIT", "분당 한도 초과"),
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1", timeout=3.0)
        await pilot.press("e")
        ok = await _wait_for(
            lambda: "error" in app.screen.query_one("#status", Static).classes,
            timeout=3.0,
        )
        assert ok
        assert "RATE_LIMIT" in app.screen.last_status
        # 행은 비어있어야
        assert app.screen.query_one("#entries-table", DataTable).row_count == 0


@pytest.mark.asyncio
async def test_home_open_entries_without_active_section_shows_error():
    """sections-list 가 빈 응답이면 활성 섹션 없음 → action_open_entries 가
    EntriesScreen 으로 push 하지 않고 화면 status 에 에러를 남긴다.

    OptionList placeholder 가 'e' 키를 흡수할 수 있어 키 입력 대신 action
    을 직접 호출 — 사용자 시나리오 (정상 sections + 'e') 는 별도 테스트가
    이미 커버.
    """
    fake = FakeClient(sections=[])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        from whooing_tui.screens.home import HomeScreen
        await _wait_for(lambda: isinstance(app.screen, HomeScreen), timeout=2.0)
        # 첫 mount 의 sections worker 가 끝나길 (status bar 가 placeholder 메시지로 settled)
        await _wait_for(
            lambda: "(섹션 없음)" in app.screen.last_status
            or "섹션이 없습니다" in app.screen.last_status,
            timeout=2.0,
        )
        app.screen.action_open_entries()  # type: ignore[attr-defined]
        await pilot.pause()
        # EntriesScreen 으로 push 되면 안 됨
        assert not isinstance(app.screen, EntriesScreen)
        bar = app.screen.query_one("#status", Static)
        assert "error" in bar.classes
        assert "활성 섹션이 없습니다" in app.screen.last_status
