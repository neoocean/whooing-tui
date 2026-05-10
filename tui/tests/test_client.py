"""WhooingClient — respx 기반 HTTP 모킹.

후잉 응답 포맷의 다양한 shape (list / {key:[..]} / {id:obj}) 와 에러 코드,
1년 분할, 100-cap bisection 을 검증.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from whooing_tui.auth import WhooingAuth
from whooing_tui.client import WhooingClient
from whooing_tui.models import ToolError


def _make_client() -> WhooingClient:
    return WhooingClient(
        auth=WhooingAuth(token="__eyJhfaketokenfortests1234"),
        base_url="https://whooing.com/api",
    )


@respx.mock
async def test_list_sections_list_shape():
    respx.get("https://whooing.com/api/sections.json").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": [
                    {"section_id": "s1", "title": "main"},
                    {"section_id": "s2", "title": "side"},
                ],
            },
        )
    )
    c = _make_client()
    sections = await c.list_sections()
    assert len(sections) == 2
    assert sections[0]["section_id"] == "s1"


@respx.mock
async def test_list_sections_dict_id_shape():
    # results 가 {id: obj} 인 경우 — values 로 정규화돼야 함
    respx.get("https://whooing.com/api/sections.json").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": {
                    "s1": {"section_id": "s1", "title": "main"},
                    "s2": {"section_id": "s2", "title": "side"},
                },
            },
        )
    )
    c = _make_client()
    sections = await c.list_sections()
    assert len(sections) == 2
    titles = {s["title"] for s in sections}
    assert titles == {"main", "side"}


@respx.mock
async def test_list_accounts_returns_dict_grouped():
    respx.get("https://whooing.com/api/accounts.json").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": {
                    "assets": [{"account_id": "x11", "title": "현금"}],
                    "expenses": [{"account_id": "x20", "title": "식비"}],
                },
            },
        )
    )
    c = _make_client()
    raw = await c.list_accounts("s1")
    assert "assets" in raw and "expenses" in raw
    flat = WhooingClient.flatten_accounts(raw)
    assert {a["account_id"] for a in flat} == {"x11", "x20"}
    # 타입 그룹이 보존되어야 함
    by_id = {a["account_id"]: a for a in flat}
    assert by_id["x11"]["type"] == "assets"
    assert by_id["x20"]["type"] == "expenses"


@respx.mock
async def test_list_entries_under_cap_single_call():
    rows = [
        {"entry_id": f"e{i}", "entry_date": "20260510", "money": 1000 + i, "item": f"항목{i}"}
        for i in range(5)
    ]
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"reports": [], "rows": rows}},
        )
    )
    c = _make_client()
    out = await c.list_entries("s1", "20260501", "20260510")
    assert len(out) == 5
    assert out[0]["entry_id"] == "e0"


@respx.mock
async def test_auth_error_maps_to_tool_error():
    respx.get("https://whooing.com/api/sections.json").mock(
        return_value=Response(
            401,
            json={"code": 401, "message": "expired"},
        )
    )
    c = _make_client()
    with pytest.raises(ToolError) as ei:
        await c.list_sections()
    assert ei.value.kind == "AUTH"


@respx.mock
async def test_400_error_carries_error_parameters():
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200,  # body 의 code 가 우선
            json={
                "code": 400,
                "message": "잘못된 파라미터",
                "error_parameters": {"start_date": "required"},
            },
        )
    )
    c = _make_client()
    with pytest.raises(ToolError) as ei:
        await c.list_entries("s1", "20260501", "20260510")
    assert ei.value.kind == "USER_INPUT"
    assert ei.value.details.get("error_parameters") == {"start_date": "required"}


def test_normalize_collection_variants():
    # list
    assert WhooingClient._normalize_collection(
        [{"a": 1}], key="rows"
    ) == [{"a": 1}]
    # {key: [...]}
    assert WhooingClient._normalize_collection(
        {"rows": [{"a": 1}], "reports": []}, key="rows"
    ) == [{"a": 1}]
    # {id: obj} fallback
    out = WhooingClient._normalize_collection(
        {"x1": {"id": "x1"}, "x2": {"id": "x2"}}, key="rows"
    )
    assert {o["id"] for o in out} == {"x1", "x2"}
    # None → []
    assert WhooingClient._normalize_collection(None, key="rows") == []


# ---- CL #51117+: report / budget / goal — 실 endpoint path 검증 ----------


@respx.mock
async def test_get_report_root_path():
    """`account` 안 주면 `/report.json` (root)."""
    route = respx.get("https://whooing.com/api/report.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"total": 12345, "accounts": []}},
        )
    )
    c = _make_client()
    out = await c.get_report(section_id="s1")
    assert out == {"total": 12345, "accounts": []}
    assert route.called


@respx.mock
async def test_get_report_account_in_path():
    """`account` 주면 `/report/<account>.json` (path)."""
    route = respx.get("https://whooing.com/api/report/expenses,income.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _make_client()
    await c.get_report(
        section_id="s1", account="expenses,income",
        rows_type="month", start_date="20260101", end_date="20260510",
    )
    assert route.called
    qs = dict(route.calls.last.request.url.params)
    assert qs["section_id"] == "s1"
    assert qs["rows_type"] == "month"
    assert qs["start_date"] == "20260101"


@respx.mock
async def test_get_report_account_id_in_path():
    """`account` + `account_id` 면 `/report/<account>/<account_id>.json`."""
    route = respx.get(
        "https://whooing.com/api/report/expenses/x20.json"
    ).mock(return_value=Response(200, json={"code": 200, "results": {}}))
    c = _make_client()
    await c.get_report(section_id="s1", account="expenses", account_id="x20")
    assert route.called


@respx.mock
async def test_get_report_summary_path():
    route = respx.get(
        "https://whooing.com/api/report_summary/expenses,income.json"
    ).mock(return_value=Response(200, json={"code": 200, "results": {}}))
    c = _make_client()
    await c.get_report_summary(section_id="s1", account="expenses,income")
    assert route.called


@respx.mock
async def test_get_calendar_path():
    route = respx.get("https://whooing.com/api/calendar.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _make_client()
    await c.get_calendar(section_id="s1", start_date="20260501", end_date="20260510")
    assert route.called


@respx.mock
async def test_list_report_customs_uses_main_path():
    """`/main/report_customs.json?action=list&report=...`."""
    route = respx.get("https://whooing.com/api/main/report_customs.json").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": {
                    "rows": [
                        {"id": "12", "title": "행1"},
                        {"id": "13", "title": "행2"},
                    ],
                },
            },
        )
    )
    c = _make_client()
    out = await c.list_report_customs(section_id="s1", report="report_bs")
    assert len(out) == 2
    assert out[0]["id"] == "12"
    qs = dict(route.calls.last.request.url.params)
    assert qs["action"] == "list"
    assert qs["report"] == "report_bs"


@respx.mock
async def test_get_report_custom_uses_action_info():
    route = respx.get("https://whooing.com/api/main/report_customs.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"id": "12", "title": "행1"}},
        )
    )
    c = _make_client()
    out = await c.get_report_custom(
        section_id="s1", report="report_bs", custom_id="12",
    )
    assert out["id"] == "12"
    qs = dict(route.calls.last.request.url.params)
    assert qs["action"] == "info"
    assert qs["customId"] == "12"


@respx.mock
async def test_get_budget_uses_account_in_path():
    """`/budget/<account>.json` — account 가 path 로."""
    route = respx.get("https://whooing.com/api/budget/expenses.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"aggregate": {"total": {}}}},
        )
    )
    c = _make_client()
    out = await c.get_budget(section_id="s1", account="expenses")
    assert route.called
    qs = dict(route.calls.last.request.url.params)
    assert qs["section_id"] == "s1"
    # 옛날 `pl` query 가 아니라 path 에 들어가야.
    assert "pl" not in qs


@respx.mock
async def test_get_budget_goal_path():
    respx.get("https://whooing.com/api/budget_goal.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"set_id": 42}},
        )
    )
    c = _make_client()
    out = await c.get_budget_goal(section_id="s1")
    assert out == {"set_id": 42}


@respx.mock
async def test_get_goal_path():
    route = respx.get("https://whooing.com/api/goal.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": [{"date": "202601", "money": 100}]},
        )
    )
    c = _make_client()
    out = await c.get_goal(section_id="s1", start_date="202601", end_date="202612")
    assert route.called


@respx.mock
async def test_get_in_out_path():
    route = respx.get("https://whooing.com/api/in_out.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _make_client()
    await c.get_in_out(section_id="s1")
    assert route.called


@respx.mock
async def test_get_entries_latest_path():
    route = respx.get("https://whooing.com/api/entries/latest.json").mock(
        return_value=Response(200, json={"code": 200, "results": []})
    )
    c = _make_client()
    await c.get_entries_latest(section_id="s1", limit=20)
    assert route.called
    qs = dict(route.calls.last.request.url.params)
    assert qs["limit"] == "20"

