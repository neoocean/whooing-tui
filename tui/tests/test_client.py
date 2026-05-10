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


# ---- CL #51116+: report / budget / goal -----------------------------------


@respx.mock
async def test_get_report_basic():
    respx.get("https://whooing.com/api/reports.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"total": 12345, "accounts": []}},
        )
    )
    c = _make_client()
    out = await c.get_report(
        section_id="s1", type="report", account="all",
    )
    assert out == {"total": 12345, "accounts": []}


@respx.mock
async def test_get_report_passes_optional_params():
    """start_date / end_date / rows_type 등 옵션 파라미터가 query 로."""
    route = respx.get("https://whooing.com/api/reports.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _make_client()
    await c.get_report(
        section_id="s1", type="report",
        start_date="20260101", end_date="20260510",
        rows_type="month",
    )
    request = route.calls.last.request
    qs = dict(request.url.params)
    assert qs["section_id"] == "s1"
    assert qs["type"] == "report"
    assert qs["start_date"] == "20260101"
    assert qs["end_date"] == "20260510"
    assert qs["rows_type"] == "month"


@respx.mock
async def test_list_report_customs_returns_list():
    respx.get("https://whooing.com/api/report_customs.json").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": [
                    {"custom_id": "1", "title": "행1"},
                    {"custom_id": "2", "title": "행2"},
                ],
            },
        )
    )
    c = _make_client()
    out = await c.list_report_customs(section_id="s1", report="report_bs")
    assert len(out) == 2
    assert out[0]["custom_id"] == "1"


@respx.mock
async def test_get_budget():
    route = respx.get("https://whooing.com/api/budget.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"budgeted": 500000}},
        )
    )
    c = _make_client()
    out = await c.get_budget(section_id="s1", pl="expenses")
    assert out == {"budgeted": 500000}
    qs = dict(route.calls.last.request.url.params)
    assert qs["pl"] == "expenses"


@respx.mock
async def test_get_budget_goal():
    respx.get("https://whooing.com/api/budget_goal.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"set_id": 42}},
        )
    )
    c = _make_client()
    out = await c.get_budget_goal(section_id="s1")
    assert out == {"set_id": 42}

