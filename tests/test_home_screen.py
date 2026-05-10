"""HomeScreen — Textual App.run_test() 기반 통합 테스트.

WhooingClient 를 fake 로 대체해 실 후잉 호출 없이 화면 흐름을 검증한다.
검증 포인트:
  - 첫 mount 후 sections-list 가 호출되어 OptionList 에 채워진다.
  - 첫 섹션이 자동 활성화되어 SessionState 와 Tree 가 동기화된다.
  - 사용자가 다른 섹션을 선택하면 SessionState.section_id 가 갱신되고
    accounts-list 가 다시 호출된다.
  - 후잉 ToolError 발생 시 status bar 가 error 클래스를 갖는다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import OptionList, Static, Tree

from whooing_tui.app import WhooingTuiApp
from whooing_tui.client import WhooingClient
from whooing_tui.models import ToolError


class FakeClient:
    """WhooingClient 동작을 흉내내는 in-memory fake.

    실 WhooingClient 와 같은 메서드 시그니처만 맞추고, 호출 횟수와
    인자를 기록한다. 테스트는 결과(`returns`) 또는 예외(`raises`) 를
    필드에 셋업한 뒤 화면을 띄운다.
    """

    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
        accounts_by_section: dict[str, dict[str, Any]] | None = None,
        sections_error: ToolError | None = None,
        accounts_error: ToolError | None = None,
    ) -> None:
        self.sections = sections or []
        self.accounts_by_section = accounts_by_section or {}
        self.sections_error = sections_error
        self.accounts_error = accounts_error
        self.list_sections_calls = 0
        self.list_accounts_calls: list[str] = []

    async def list_sections(self) -> list[dict[str, Any]]:
        self.list_sections_calls += 1
        if self.sections_error is not None:
            raise self.sections_error
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        self.list_accounts_calls.append(section_id)
        if self.accounts_error is not None:
            raise self.accounts_error
        return self.accounts_by_section.get(section_id, {})


def _two_sections_one_with_accounts() -> FakeClient:
    return FakeClient(
        sections=[
            {"section_id": "s1", "title": "main"},
            {"section_id": "s2", "title": "side"},
        ],
        accounts_by_section={
            "s1": {
                "assets": [{"account_id": "x11", "title": "현금"}],
                "expenses": [
                    {"account_id": "x20", "title": "식비"},
                    {"account_id": "x21", "title": "교통비"},
                ],
            },
            "s2": {
                "assets": [{"account_id": "x12", "title": "통장"}],
            },
        },
    )


async def _wait_for(predicate, *, timeout: float = 2.0, interval: float = 0.02):
    """짧은 지연을 두고 비동기 worker 결과가 화면에 반영되길 기다린다.

    Textual 의 @work 데코레이터는 await 가 아닌 후속 task 를 띄우므로,
    테스트는 결과가 나타날 때까지 polling 한다.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_home_screen_loads_sections_and_auto_activates_first():
    fake = _two_sections_one_with_accounts()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # 첫 mount 후 sections + accounts 둘 다 로드될 때까지 대기
        ok = await _wait_for(
            lambda: fake.list_sections_calls >= 1
            and fake.list_accounts_calls
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        assert ok, (
            f"sections={fake.list_sections_calls} "
            f"accounts={fake.list_accounts_calls} "
            f"section_id={app.session.section_id}"
        )

        # OptionList 에 두 섹션이 들어갔는지
        ol = app.screen.query_one("#sections-list", OptionList)
        assert ol.option_count == 2

        # SessionState 의 양방향 인덱스가 채워졌는지
        assert app.session.title_of("x20") == "식비"
        assert app.session.id_of("식비") == "x20"

        # status bar 에 success 메시지 (error 클래스 없어야)
        status = app.screen.query_one("#status", Static)
        assert "error" not in status.classes
        await pilot.pause()


@pytest.mark.asyncio
async def test_home_screen_select_other_section_triggers_accounts_reload():
    fake = _two_sections_one_with_accounts()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: app.session.section_id == "s1"
            and "s1" in fake.list_accounts_calls,
            timeout=3.0,
        )

        # 두 번째 섹션을 선택 — OptionList highlight 를 옮기고 enter
        ol = app.screen.query_one("#sections-list", OptionList)
        ol.highlighted = 1
        await pilot.pause()
        await pilot.press("enter")

        ok = await _wait_for(
            lambda: app.session.section_id == "s2"
            and "s2" in fake.list_accounts_calls,
            timeout=3.0,
        )
        assert ok, (
            f"section_id={app.session.section_id} "
            f"accounts_calls={fake.list_accounts_calls}"
        )

        # s1 캐시가 비워지고 s2 의 계정이 들어왔는지
        assert app.session.id_of("식비") is None  # 이전 섹션 항목은 제거
        assert app.session.id_of("통장") == "x12"


@pytest.mark.asyncio
async def test_home_screen_sections_error_shows_status_error():
    fake = FakeClient(
        sections_error=ToolError("AUTH", "토큰 만료"),
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        ok = await _wait_for(
            lambda: "error" in app.screen.query_one("#status", Static).classes,
            timeout=3.0,
        )
        assert ok
        # HomeScreen.last_status 가 평문 메시지를 보관 — Static 의 사적
        # API 에 의존하지 않는다.
        assert "AUTH" in app.screen.last_status
        # accounts 는 호출되지 않아야 함 (sections 단계에서 실패)
        assert fake.list_accounts_calls == []


@pytest.mark.asyncio
async def test_home_screen_accounts_error_shows_status_error():
    fake = FakeClient(
        sections=[{"section_id": "s1", "title": "main"}],
        accounts_error=ToolError("UPSTREAM", "후잉 서버 오류"),
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # 자동 activate 가 accounts 호출을 만들고, 그 결과가 에러
        ok = await _wait_for(
            lambda: fake.list_accounts_calls
            and "error" in app.screen.query_one("#status", Static).classes,
            timeout=3.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_home_screen_empty_sections_disables_picker():
    fake = FakeClient(sections=[])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        ok = await _wait_for(
            lambda: fake.list_sections_calls >= 1, timeout=3.0,
        )
        assert ok
        ol = app.screen.query_one("#sections-list", OptionList)
        assert ol.option_count == 1  # __empty__ disabled placeholder
        # accounts 는 호출되면 안 됨
        assert fake.list_accounts_calls == []


@pytest.mark.asyncio
async def test_home_screen_refresh_action_reloads_active_section():
    fake = _two_sections_one_with_accounts()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: app.session.section_id == "s1"
            and "s1" in fake.list_accounts_calls,
            timeout=3.0,
        )
        before = len(fake.list_accounts_calls)

        await pilot.press("r")  # action_refresh
        ok = await _wait_for(
            lambda: len(fake.list_accounts_calls) > before, timeout=3.0,
        )
        assert ok
        # 활성 섹션이 같은 s1 — accounts 만 다시 호출
        assert fake.list_accounts_calls[-1] == "s1"
