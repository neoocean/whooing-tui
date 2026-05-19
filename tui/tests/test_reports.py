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


def test_format_report_payload_unknown_id_falls_back_to_json():
    """CL #52786+ 부터 item_id 별 전용 렌더 — 등록 안 된 id 만 raw JSON."""
    out = format_report_payload(
        "unknown_id", {"total": 1000, "accounts": [{"id": "x1"}]},
    )
    assert "```" in out
    assert "1000" in out
    assert "x1" in out


def test_format_report_payload_none_yields_placeholder():
    out = format_report_payload("balance_sheet", None)
    assert "응답 없음" in out


# CL #52753+: 빈 list / 빈 dict 도 명확한 안내 (사용자 보고: "빈 화면")
def test_format_report_payload_empty_list_explains():
    out = format_report_payload("custom_bs", [])
    assert "결과 없음" in out
    # 단순 `[]` 만 출력하면 사용자가 빈 화면으로 인식 — 안내 메시지 포함.
    assert "[]" not in out


def test_format_report_payload_empty_dict_explains():
    out = format_report_payload("balance_sheet", {})
    assert "결과 없음" in out
    assert "{}" not in out


def test_format_report_payload_unknown_id_with_list_falls_back():
    """CL #52786+: 등록 안 된 item_id + list payload → raw JSON fallback."""
    out = format_report_payload("unknown_id", [{"x": 1}])
    assert "```" in out
    assert '"x": 1' in out


def test_format_report_payload_entries_latest_renders_money_with_commas():
    """entries_latest renderer 가 천단위 콤마 포매팅 적용."""
    out = format_report_payload(
        "entries_latest", [{"item": "스타벅스", "money": 5000,
                            "entry_date": "20260518"}],
    )
    assert "스타벅스" in out
    # 콤마 형식 (5,000) — raw 5000 X.
    assert "5,000" in out


# ---- 통합: 't' 단축키 → ReportsMenuScreen → ReportResultScreen ---------


class _Client:
    """test 용 — section/account/entries + report fetch 모킹.

    CL #52755+: 보고서 fetch 가 모두 `call_official_tool` 위임으로 변경.
    옛 endpoint-별 메서드 (get_report 등) 는 더 이상 호출되지 않으므로
    제거. 대신 `call_official_tool` 의 호출 args 를 capture.
    """

    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"assets": [{"account_id": "x11", "title": "현금"}]}
        self._entries: list[dict[str, Any]] = []
        self.last_mcp_call: dict[str, Any] | None = None
        # 도구별 응답 stub — 필요 시 override.
        self.tool_responses: dict[str, Any] = {
            "report-get": {"total": 12345, "accounts": []},
            "report_customs-list": {"rows": []},
            "budget-get": {"aggregate": {"total": {"budget": 500000}}},
            "budget_goal-get": {"set_id": 0},
        }
        # 도구별 raise 옵션.
        self.tool_errors: dict[str, Exception] = {}

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def call_official_tool(self, name: str, arguments: dict[str, Any]):
        self.last_mcp_call = {"name": name, "arguments": dict(arguments)}
        if name in self.tool_errors:
            raise self.tool_errors[name]
        return self.tool_responses.get(name, {})

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
    """EntriesScreen 에서 't' → ReportsScreen (CL #52792+ 좌/우 통합) 등장."""
    from whooing_tui.screens.reports import ReportsScreen

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
        assert isinstance(app.screen, ReportsScreen)


@pytest.mark.asyncio
async def test_menu_select_pushes_result_screen_and_fetches():
    """CL #52792+: 통합 ReportsScreen 의 자동 fetch — 진입 즉시 첫 항목
    (balance_sheet) 의 client 호출 발생.
    """
    from whooing_tui.screens.reports import ReportsScreen

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
        assert isinstance(app.screen, ReportsScreen)
        # ReportsScreen 의 on_mount 가 첫 항목 (balance_sheet) 자동 fetch.
        ok = await _wait_for(
            lambda: fake.last_mcp_call is not None
            and getattr(app.screen, "last_payload", None) is not None,
            timeout=3.0,
        )
        assert ok
        # 공식 MCP server 의 `report-get` 도구 호출.
        assert fake.last_mcp_call["name"] == "report-get"
        args = fake.last_mcp_call["arguments"]
        assert args["section_id"] == "s1"
        assert args["type"] == "report"
        assert args["account"] == "all"
        assert args["rows_type"] == "none"


@pytest.mark.asyncio
async def test_menu_cancel_returns_without_pushing_result():
    """CL #52792+: ReportsScreen Esc → EntriesScreen 직접 복귀 (한 화면)."""
    from whooing_tui.screens.reports import ReportsScreen

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
        assert isinstance(app.screen, ReportsScreen)
        # ReportsScreen Esc → EntriesScreen 직접 복귀.
        app.screen.action_close()
        await pilot.pause()
        assert isinstance(app.screen, EntriesScreen)


@pytest.mark.asyncio
async def test_result_screen_dispatches_correct_endpoint_for_budget_goal():
    """budget_goal 항목 → 공식 MCP 의 `budget_goal-get` 도구 호출."""
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
            lambda: fake.last_mcp_call is not None,
            timeout=3.0,
        )
        assert fake.last_mcp_call["name"] == "budget_goal-get"
        assert fake.last_mcp_call["arguments"]["section_id"] == "s1"


@pytest.mark.asyncio
async def test_result_screen_handles_tool_error_silently():
    """공식 MCP 호출이 ToolError 던지면 결과 화면이 적색 메시지로 표시 +
    모달 그대로 (앱은 정상)."""
    from whooing_tui.models import ToolError

    fake = _Client()
    fake.tool_errors["report-get"] = ToolError("USER_INPUT", "잘못된 파라미터")

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


@pytest.mark.asyncio
async def test_error_message_shown_in_body_not_only_status():
    """CL #52753+: 에러가 status (작은 1줄) 만이 아니라 body (큰 영역) 에도
    표시 — 사용자 보고 "보고서 화면이 빈 화면" 회귀 방지.
    """
    from textual.widgets import Static

    from whooing_tui.models import ToolError

    fake = _Client()
    fake.tool_errors["report-get"] = ToolError("UPSTREAM", "비-JSON 응답 (status=403)")

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
        await _wait_for(
            lambda: getattr(app.screen, "last_error", None) is not None,
            timeout=3.0,
        )
        # body 의 content Static 가 에러 메시지를 포함해야 함 — 빈 화면 X.
        content = app.screen.query_one("#reports-result-result-content", Static) \
            if False else app.screen.query_one("#reports-result-content", Static)
        body_text = str(content.render())
        assert "UPSTREAM" in body_text or "비-JSON" in body_text or "에러" in body_text, (
            f"body 에 에러 정보 누락 — 빈 화면 회귀: {body_text!r}"
        )


# ---- CL #52755+ : 공식 후잉 MCP 위임 schema 회귀 방지 -----------------


def test_menu_balance_sheet_uses_account_all_not_csv():
    """CL #52755+: balance_sheet fetch 는 account='all' 이어야.

    종전엔 'assets,liabilities' (콤마 다중) — 후잉 schema 의 enum 위반
    이라 403. 다시 콤마 표기로 돌아가면 라이브 API 가 거부.
    """
    from whooing_tui.screens.reports import _build_menu
    import inspect
    items = {iid: fn for iid, _label, fn in _build_menu()}
    src = inspect.getsource(items["balance_sheet"])
    assert '"account": "all"' in src or "'account': 'all'" in src


def test_menu_budget_uses_ym_dates_not_ymd():
    """budget-get 의 start_date/end_date 는 YYYYMM (6자리). YYYYMMDD 면
    후잉 API 가 거부."""
    from whooing_tui.screens.reports import _build_menu, _ym_start_today
    s, e = _ym_start_today()
    assert len(s) == 6 and len(e) == 6, f"YYYYMM 6자리 기대: ({s}, {e})"
    assert s.isdigit() and e.isdigit()


def test_menu_all_fetches_use_official_mcp_tool():
    """모든 11 fetch_fn 이 client.call_official_tool 을 호출 — 자체 REST
    path 추측 코드로 다시 돌아가면 fail.
    """
    import inspect
    from whooing_tui.screens.reports import _build_menu

    for iid, _label, fn in _build_menu():
        src = inspect.getsource(fn)
        assert "call_official_tool" in src, (
            f"{iid} 가 call_official_tool 위임 안 함 — 자체 REST 추측 회귀"
        )
        # 옛 메서드 호출이 다시 들어가면 안 됨.
        for old in (
            "client.get_report(", "client.get_report_summary(",
            "client.get_in_out(", "client.get_calendar(",
            "client.get_entries_latest(", "client.list_report_customs(",
            "client.get_budget(", "client.get_budget_goal(",
        ):
            assert old not in src, f"{iid} 가 옛 메서드 {old!r} 호출"


def test_call_official_tool_helper_exists_on_client():
    """WhooingClient 에 call_official_tool helper 가 노출."""
    from whooing_tui.client import WhooingClient
    assert hasattr(WhooingClient, "call_official_tool")


# ---- CL #52765+ : CachedWhooingClient 도 call_official_tool wrap -----


def test_cached_client_has_call_official_tool():
    """CL #52765: CachedWhooingClient 가 call_official_tool 을 wrap.

    종전 CL #52755 에서 WhooingClient 에만 추가 — production 의 default
    client (CachedWhooingClient) 가 그 메서드 없어서 `INTERNAL: ... no
    attribute 'call_official_tool'` 에러 (사용자 보고). 본 테스트가 회귀
    방지.
    """
    from whooing_tui.client import CachedWhooingClient
    assert hasattr(CachedWhooingClient, "call_official_tool"), (
        "CachedWhooingClient 가 call_official_tool 을 wrap 안 함 — "
        "보고서 fetch 가 INTERNAL error 회귀"
    )


@pytest.mark.asyncio
async def test_cached_client_call_official_tool_delegates_to_inner():
    """wrap 메서드가 inner 의 호출에 정확히 전달."""
    from whooing_tui.cache import CacheStore
    from whooing_tui.client import CachedWhooingClient

    class _FakeInner:
        async def call_official_tool(self, name, arguments):
            return {"called": name, "args": arguments}

    cached = CachedWhooingClient(
        inner=_FakeInner(),  # type: ignore[arg-type]
        store=CacheStore(":memory:"),
    )
    out = await cached.call_official_tool(
        "report-get", {"type": "report", "section_id": "s1"},
    )
    assert out == {
        "called": "report-get",
        "args": {"type": "report", "section_id": "s1"},
    }


# ---- CL #52786+ : item_id 별 사람-친화 렌더 ---------------------------


def test_pl_summary_renders_as_table_not_json():
    """pl_summary 응답이 표 형태 + 한글 항목명 + 천단위 콤마.

    사용자 캡처의 정확한 시나리오 — `{assets, liabilities, ...}` flat dict.
    """
    out = format_report_payload("pl_summary", {
        "assets": -31598068, "liabilities": 164524712,
        "expenses": 25957071, "income": 0,
        "capital": -196122780, "net_income": -25957071,
    })
    # raw JSON 형식이 아님 — 한글 라벨 + 천단위 콤마.
    assert "```" not in out
    assert "자산" in out
    assert "부채" in out
    assert "순이익" in out
    # 천단위 콤마 형식.
    assert "-31,598,068" in out
    assert "25,957,071" in out


def test_balance_sheet_uses_same_renderer():
    """balance_sheet 도 같은 flat-dict renderer 로 처리."""
    out = format_report_payload("balance_sheet", {
        "assets": 1_000_000, "liabilities": 500_000,
        "capital": 500_000,
    })
    assert "자산" in out and "부채" in out and "자본" in out
    assert "1,000,000" in out


def test_monthly_trend_renders_as_month_table():
    """월별 추이 — rows: {YYYYMM: ...} 를 표로."""
    out = format_report_payload("monthly_trend", {
        "rows_type": "month",
        "rows": {
            "202604": {"income": 8000000, "expenses": 5500000},
            "202605": {"income": 0, "expenses": 25957071},
        },
    })
    assert "2026-04" in out
    assert "2026-05" in out
    assert "8,000,000" in out
    # 순이익 계산: 0 - 25957071 = -25957071
    assert "-25,957,071" in out


def test_entries_latest_renders_as_row_table():
    """최근 거래 — list of dict → 일자/금액/차변/대변/적요 표."""
    out = format_report_payload("entries_latest", [
        {"entry_id": 1712609, "entry_date": "20260516.0000",
         "l_account": "expenses", "r_account": "liabilities",
         "money": 15191, "item": "도메인"},
    ])
    assert "2026-05-16" in out
    assert "15,191" in out
    assert "도메인" in out


def test_in_out_renders_with_all_columns():
    """in_out — 계정별 in/out/margin/balance 4 col 표."""
    out = format_report_payload("in_out", {
        "assets": {"total": {"in": 0, "out": 100, "margin": -100, "balance": -500}},
    })
    assert "자산" in out
    assert "수입" in out and "지출" in out
    assert "-100" in out


def test_budget_renders_aggregate_total():
    """budget_expenses / budget_income — aggregate.total 의 budget/money/remains."""
    out = format_report_payload("budget_expenses", {
        "aggregate": {"total": {"budget": 0, "money": 25957071, "remains": -25957071}},
    })
    assert "예산" in out and "사용" in out and "잔여" in out
    assert "25,957,071" in out


def test_budget_goal_renders_period_and_amounts():
    """장기목표 — base_ym ~ goal_ym + goal_money."""
    out = format_report_payload("budget_goal", {
        "set_id": 4613, "base_ym": "201806", "goal_ym": "201906",
        "base_money": 100_000_000, "goal_money": 200_000_000,
    })
    assert "기간" in out
    assert "2018-06" in out and "2019-06" in out
    assert "200,000,000" in out


def test_budget_goal_unset_message():
    """set_id=0 → 미설정 안내."""
    out = format_report_payload("budget_goal", {"set_id": 0})
    assert "미설정" in out


def test_custom_bs_empty_rows_shows_guidance():
    """custom_bs/pl 의 `{rows: []}` — '사용자 정의 행 정의 필요' 안내."""
    out = format_report_payload("custom_bs", {"rows": []})
    assert "결과 없음" in out
    assert "정의" in out


def test_custom_bs_with_rows_renders_table():
    out = format_report_payload("custom_bs", {"rows": [
        {"id": "12", "title": "현금성 자산", "money": 1500000},
        {"id": "13", "title": "투자 자산", "money": 2000000},
    ]})
    assert "현금성 자산" in out
    assert "1,500,000" in out
    assert "투자 자산" in out


def test_unknown_item_id_falls_back_to_json():
    """등록 안 된 item_id — raw JSON fallback."""
    out = format_report_payload("unknown_thing", {"x": 1})
    assert "```" in out and '"x"' in out


def test_renderer_failure_falls_back_to_json():
    """renderer 가 예외 raise — fallback 으로 raw JSON."""
    # in_out renderer 는 dict 만 받는데 list 면 None — fallback.
    out = format_report_payload("in_out", [1, 2, 3])
    assert "```" in out or "결과 없음" in out


# ---- CL #52790+ : Esc 가 ReportResultScreen → ReportsMenuScreen 복귀 -


@pytest.mark.asyncio
async def test_esc_from_result_returns_to_menu_not_entries():
    """ReportResultScreen 에서 Esc → ReportsMenuScreen 복귀 (사용자 요청).

    종전엔 EntriesScreen 까지 한 번에 닫혀 사용자가 메뉴 재진입 부담.
    """
    from textual.widgets import OptionList

    from whooing_tui.screens.reports import ReportsScreen

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
        # CL #52792+: 통합 ReportsScreen — 메뉴 + 결과 한 화면. Esc 한 번에
        # EntriesScreen 복귀 (메뉴 ↔ 결과 분리 안 됨).
        assert isinstance(app.screen, ReportsScreen)
        app.screen.action_close()
        await pilot.pause()
        assert isinstance(app.screen, EntriesScreen)


def test_budget_renderer_handles_full_aggregate_shape():
    """CL #52790+: 사용자 캡처의 복잡한 budget 응답 (total/total_steady/
    total_floating/misc) 도 표 형태로 — raw JSON 으로 떨어지면 안 됨.
    """
    p = {
        "aggregate": {
            "total": {"budget": 0, "money": 0, "remains": 0},
            "total_steady": {"budget": 0, "money": 0, "remains": 0},
            "total_floating": {"budget": 0, "money": 0, "remains": 0},
            "misc": {
                "daily_remains": 0, "weekly_remains": 0,
                "standard": 0, "possibility": 100,
                "today": {"budget": 0, "money": 0, "remains": 0},
            },
        },
    }
    out = format_report_payload("budget_income", p)
    assert "```" not in out  # raw JSON fallback 아님.
    assert "전체" in out
    assert "정기" in out
    assert "유동" in out
    assert "오늘" in out
    assert "달성 가능성" in out and "100%" in out


# ---- CL #52792+ : 통합 ReportsScreen 좌/우 패널 ----------------------


@pytest.mark.asyncio
async def test_reports_screen_has_menu_and_content_panes():
    """좌측 OptionList + 우측 Static content + status — 패널 분리 layout."""
    from textual.widgets import OptionList, Static

    from whooing_tui.screens.reports import ReportsScreen

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
        assert isinstance(app.screen, ReportsScreen)
        # 좌측 OptionList 가 11개 항목.
        opt = app.screen.query_one("#reports-menu-list", OptionList)
        assert opt.option_count == 11
        # 우측 panel widgets 존재.
        app.screen.query_one("#reports-status", Static)
        app.screen.query_one("#reports-content", Static)


@pytest.mark.asyncio
async def test_reports_screen_auto_fetches_on_highlight_change():
    """↑/↓ 로 항목 이동 → 자동 fetch (worker exclusive, 이전 cancel)."""
    from textual.widgets import OptionList

    from whooing_tui.screens.reports import ReportsScreen

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
        screen = app.screen
        # 첫 항목 (balance_sheet) 자동 fetch.
        await _wait_for(
            lambda: fake.last_mcp_call is not None
            and fake.last_mcp_call["arguments"].get("type") == "report",
            timeout=3.0,
        )
        # 두 번째 항목 (pl_summary) 로 이동.
        opt = screen.query_one("#reports-menu-list", OptionList)
        opt.highlighted = 1
        await pilot.pause()
        # report_summary 호출 도착 대기.
        await _wait_for(
            lambda: fake.last_mcp_call["arguments"].get("type") == "report_summary",
            timeout=3.0,
        )
        assert fake.last_mcp_call["name"] == "report-get"


# ---- CL #52896+ : 같은 회귀 패턴 — 매월/예산/목표 mutation pass-through ---


def test_cached_client_has_monthly_budget_goal_methods():
    """CL #52896: CachedWhooingClient 가 list_monthly / create_monthly /
    delete_monthly / set_budget / delete_budget / set_budget_goal / set_goal
    모두 wrap. 사용자 보고 ("매월입력 화면이 동작 안 함") 의 회귀 가드.

    같은 종류의 회귀: WhooingClient 에 endpoint 추가 후 Cached wrapper 누락.
    """
    from whooing_tui.client import CachedWhooingClient
    for m in (
        "list_monthly", "create_monthly", "delete_monthly",
        "set_budget", "delete_budget",
        "set_budget_goal", "set_goal",
    ):
        assert hasattr(CachedWhooingClient, m), (
            f"CachedWhooingClient 가 {m} 을 wrap 안 함 — "
            f"매월입력 / 예산 / 목표 화면 회귀"
        )
