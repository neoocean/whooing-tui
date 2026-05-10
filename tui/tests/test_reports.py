"""ReportsMenuScreen + ReportResultScreen + WhooingClient report 메서드.

CL #51116+. 풀다운 메뉴 → 결과 팝업 흐름 + client 단위 테스트.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.reports import (
    ReportResultScreen,
    ReportsMenuScreen,
    _build_menu,
    format_report_payload,
)


# ---- _build_menu / format_report_payload (단위) -----------------------


def test_build_menu_returns_expected_items():
    items = _build_menu()
    ids = [iid for iid, _, _ in items]
    # 사용자 답변에서 선택한 보고서들 모두 포함.
    assert "balance_sheet" in ids
    assert "monthly_trend" in ids
    assert "custom_bs" in ids
    assert "budget_expenses" in ids
    assert "budget_goal" in ids


def test_build_menu_labels_are_korean_and_human_readable():
    for _iid, label, _fn in _build_menu():
        assert label  # not empty
        # 라벨에 ASCII-only 가 아닌 한글이 들어 있어야 (사용자 친화)
        assert any(ord(c) > 127 for c in label)


def test_format_report_payload_dict_emits_indented_json():
    out = format_report_payload(
        "balance_sheet", {"total": 1000, "accounts": [{"id": "x1"}]},
    )
    assert "```" in out
    assert "1000" in out
    assert "x1" in out


def test_format_report_payload_none_yields_placeholder():
    out = format_report_payload("balance_sheet", None)
    assert "응답 없음" in out


def test_format_report_payload_list_works():
    out = format_report_payload(
        "entries_latest", [{"item": "스타벅스", "money": 5000}],
    )
    assert "스타벅스" in out
    assert "5000" in out


# ---- 통합: 't' 단축키 → ReportsMenuScreen → ReportResultScreen ---------


class _Client:
    """test 용 — section/account/entries + report fetch 모킹."""

    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"assets": [{"account_id": "x11", "title": "현금"}]}
        self._entries: list[dict[str, Any]] = []
        self.last_report_call: dict[str, Any] | None = None

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def get_report(self, **kwargs):
        self.last_report_call = {"endpoint": "report", **kwargs}
        return {"total": 12345, "accounts": []}

    async def get_report_summary(self, **kwargs):
        self.last_report_call = {"endpoint": "report_summary", **kwargs}
        return {"rows_type": "none", "aggregate": {"expenses": 0}}

    async def get_in_out(self, **kwargs):
        self.last_report_call = {"endpoint": "in_out", **kwargs}
        return {"aggregate": {}}

    async def get_calendar(self, **kwargs):
        self.last_report_call = {"endpoint": "calendar", **kwargs}
        return {"aggregate": {}, "rows": {}}

    async def get_entries_latest(self, **kwargs):
        self.last_report_call = {"endpoint": "entries_latest", **kwargs}
        return [{"entry_id": "e1"}]

    async def list_report_customs(self, **kwargs):
        self.last_report_call = {"endpoint": "report_customs", **kwargs}
        return [{"id": "12", "title": "행1", "money": 100}]

    async def get_budget(self, **kwargs):
        self.last_report_call = {"endpoint": "budget", **kwargs}
        return {"aggregate": {"total": {"budget": 500000}}}

    async def get_budget_goal(self, **kwargs):
        self.last_report_call = {"endpoint": "budget_goal", **kwargs}
        return {"set_id": 0}

    # mutation stubs — 본 테스트 셋에서는 안 쓰임.
    async def create_entry(self, **kw):
        return {"entry_id": "n", **kw}

    async def update_entry(self, **kw):
        return {**kw}

    async def delete_entry(self, **kw):
        return {}


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_t_key_pushes_reports_menu():
    """EntriesScreen 에서 't' → ReportsMenuScreen 등장."""
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_reports()
        await pilot.pause()
        assert isinstance(app.screen, ReportsMenuScreen)


@pytest.mark.asyncio
async def test_menu_select_pushes_result_screen_and_fetches():
    """메뉴에서 항목 선택 → ReportResultScreen + 클라이언트 호출."""
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_reports()
        await pilot.pause()
        assert isinstance(app.screen, ReportsMenuScreen)
        # balance_sheet 선택 — dismiss 결과로 EntriesScreen._on_pick 가
        # ReportResultScreen 을 push 한다.
        app.screen.dismiss(("balance_sheet", "재무상태표"))
        await pilot.pause()
        assert isinstance(app.screen, ReportResultScreen)
        # fetch 가 worker 로 진행 — 잠시 기다려 client 호출 + payload 도착.
        ok = await _wait_for(
            lambda: fake.last_report_call is not None
            and getattr(app.screen, "last_payload", None) is not None,
            timeout=3.0,
        )
        assert ok
        assert fake.last_report_call["endpoint"] == "report"
        assert fake.last_report_call["section_id"] == "s1"
        # CL #51117+: type 파라미터는 제거됐고, balance_sheet 메뉴는
        # account="assets,liabilities" + rows_type="none" 으로 호출.
        assert fake.last_report_call["account"] == "assets,liabilities"
        assert fake.last_report_call["rows_type"] == "none"


@pytest.mark.asyncio
async def test_menu_cancel_returns_without_pushing_result():
    """메뉴에서 Esc — dismiss(None) — 결과 화면 뜨지 않음."""
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_reports()
        await pilot.pause()
        assert isinstance(app.screen, ReportsMenuScreen)
        app.screen.dismiss(None)
        await pilot.pause()
        # 다시 EntriesScreen
        assert isinstance(app.screen, EntriesScreen)
        # client report 호출 없음
        assert fake.last_report_call is None


@pytest.mark.asyncio
async def test_result_screen_dispatches_correct_endpoint_for_budget_goal():
    """budget_goal 항목 → get_budget_goal endpoint 호출."""
    fake = _Client()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # 메뉴 거치지 않고 직접 결과 화면 push — fetch_fn dispatch 만 검증.
        from whooing_tui.screens.reports import ReportResultScreen
        await app.push_screen(
            ReportResultScreen(
                fake, app.session, item_id="budget_goal",
                label="장기목표 설정",
            ),
        )
        await _wait_for(
            lambda: fake.last_report_call is not None,
            timeout=3.0,
        )
        assert fake.last_report_call["endpoint"] == "budget_goal"
        assert fake.last_report_call["section_id"] == "s1"


@pytest.mark.asyncio
async def test_result_screen_handles_tool_error_silently():
    """후잉 API 가 ToolError 던지면 결과 화면이 적색 메시지로 표시 + 모달
    그대로 (앱은 정상)."""
    from whooing_tui.models import ToolError

    fake = _Client()

    async def _err_get_report(**kwargs):
        raise ToolError("USER_INPUT", "잘못된 파라미터")

    fake.get_report = _err_get_report  # type: ignore[assignment]

    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        from whooing_tui.screens.reports import ReportResultScreen
        await app.push_screen(
            ReportResultScreen(
                fake, app.session, item_id="balance_sheet", label="재무상태표",
            ),
        )
        ok = await _wait_for(
            lambda: getattr(app.screen, "last_error", None) is not None,
            timeout=3.0,
        )
        assert ok
        assert "USER_INPUT" in app.screen.last_error  # type: ignore[union-attr]
        assert isinstance(app.screen, ReportResultScreen)
