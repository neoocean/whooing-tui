"""WhooingClient.create_entry / update_entry / delete_entry — respx mocks.

후잉 mutation endpoint 의 정확한 path 는 라이브 검증 전이라 RESTful 가정
(`POST /entries.json`, `PUT /entries/<id>.json`, `DELETE /entries/<id>.json`)
으로 본 클라이언트가 호출하는 것을 검증. live 검증에서 path 가 다르면
client.py 의 `_ENTRIES_PATH` / `_entry_path` 만 조정하면 된다.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from whooing_tui.auth import WhooingAuth
from whooing_tui.client import WhooingClient, _coerce_dict
from whooing_tui.models import ToolError


def _client() -> WhooingClient:
    return WhooingClient(
        auth=WhooingAuth(token="__eyJhfaketokenfortests1234"),
        base_url="https://whooing.com/api",
    )


# CL #52918+: body 가 form-urlencoded 로 전환 — JSON 이 아닌 query-string
# 형식으로 디코드해 비교.
def _parse_form(req) -> dict[str, str]:
    """form-urlencoded request body 를 dict 으로."""
    from urllib.parse import parse_qs, unquote_to_bytes
    raw = req.read().decode("utf-8")
    # parse_qs 는 list[str] 을 반환 — 단일값으로 평탄화.
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


@respx.mock
async def test_create_entry_posts_expected_body():
    route = respx.post("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"entry_id": "e_new_001"}},
        )
    )
    c = _client()
    out = await c.create_entry(
        section_id="s1",
        l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x11",
        money=12000, item="스타벅스", memo="오후",
        entry_date="20260510",
    )
    assert out.get("entry_id") == "e_new_001"
    assert route.call_count == 1
    req = route.calls[0].request
    # CL #52911+: section_id 가 query 로도 전송.
    assert b"section_id=s1" in req.url.query
    # CL #52918+: body 가 form-urlencoded — Content-Type 검증 + 필드 확인.
    assert b"application/x-www-form-urlencoded" in (
        req.headers.get("content-type", "").encode()
    )
    body = _parse_form(req)
    assert body == {
        "section_id": "s1",
        "l_account": "expenses", "l_account_id": "x20",
        "r_account": "assets", "r_account_id": "x11",
        "money": "12000",  # form-encoded 는 모두 문자열.
        "item": "스타벅스", "memo": "오후",
        "entry_date": "20260510",
    }


@respx.mock
async def test_create_entry_omits_optional_empty_fields():
    """item / memo / entry_date 가 빈 값이면 body 에 포함하지 않는다."""
    route = respx.post("https://whooing.com/api/entries.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"entry_id": "e1"}})
    )
    c = _client()
    await c.create_entry(
        section_id="s1",
        l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x11",
        money=1000,
    )
    body = _parse_form(route.calls[0].request)
    assert "item" not in body
    assert "memo" not in body
    assert "entry_date" not in body


@respx.mock
async def test_update_entry_puts_only_changed_fields():
    route = respx.put("https://whooing.com/api/entries/e123.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"entry_id": "e123"}})
    )
    c = _client()
    await c.update_entry(
        section_id="s1", entry_id="e123",
        money=99000, item="수정된 적요",
    )
    req = route.calls[0].request
    # CL #52911+: section_id 가 query param 으로도 전송.
    assert b"section_id=s1" in req.url.query
    body = _parse_form(req)
    assert body == {"section_id": "s1", "money": "99000", "item": "수정된 적요"}
    # 미지정 필드는 절대 포함되어선 안 됨 (None 으로 덮어쓰기 방지)
    for k in ("l_account", "r_account_id", "memo", "entry_date"):
        assert k not in body


@respx.mock
async def test_delete_entry_delegates_to_official_mcp():
    """CL #53015+: delete_entry 가 후잉 공식 MCP `entries-delete` 로 위임.

    사용자 2회 보고 (2026-05-19): REST DELETE 가 어떤 방식으로 section_id
    를 보내도 "section_id parameter is required" 로 거절. 보고서 endpoint
    들과 동일하게 공식 MCP server 로 위임 — `tools/call` JSON-RPC.
    """
    route = respx.post("https://whooing.com/mcp").mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "structuredContent": {"deleted": True},
                },
            },
        )
    )
    c = _client()
    out = await c.delete_entry(section_id="s7", entry_id="e999")
    assert isinstance(out, dict)
    assert route.call_count == 1
    # JSON-RPC body: method=tools/call + name=entries-delete + arguments.
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "entries-delete"
    args = body["params"]["arguments"]
    assert args == {"section_id": "s7", "entry_id": "e999"}


@respx.mock
async def test_delete_entry_falls_back_to_rest_when_mcp_fails():
    """MCP 가 JSON-RPC error 반환 → 본 메서드는 REST DELETE fallback 시도."""
    # MCP 첫 호출 — JSON-RPC error 반환.
    respx.post("https://whooing.com/mcp").mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "Method not found"},
            },
        )
    )
    # REST DELETE fallback — 정상 응답.
    delete_route = respx.delete(
        "https://whooing.com/api/entries/e1.json",
    ).mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _client()
    out = await c.delete_entry(section_id="s1", entry_id="e1")
    assert isinstance(out, dict)
    # REST DELETE 도 호출됨 (fallback path).
    assert delete_route.call_count == 1
    req = delete_route.calls[0].request
    assert req.method == "DELETE"
    assert b"section_id=s1" in req.url.query
    # form-body 도 함께.
    assert b"section_id=s1" in req.content


@respx.mock
async def test_create_entry_400_carries_user_input():
    respx.post("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200,
            json={
                "code": 400,
                "message": "잘못된 파라미터",
                "error_parameters": {"l_account_id": "required"},
            },
        )
    )
    c = _client()
    with pytest.raises(ToolError) as ei:
        await c.create_entry(
            section_id="s1",
            l_account="expenses", l_account_id="x20",
            r_account="assets", r_account_id="x11",
            money=1000,
        )
    assert ei.value.kind == "USER_INPUT"
    assert ei.value.details.get("error_parameters") == {"l_account_id": "required"}


@respx.mock
async def test_update_entry_401_maps_to_auth():
    respx.put("https://whooing.com/api/entries/e1.json").mock(
        return_value=Response(401, json={"code": 401, "message": "expired"})
    )
    c = _client()
    with pytest.raises(ToolError) as ei:
        await c.update_entry(section_id="s1", entry_id="e1", money=1)
    assert ei.value.kind == "AUTH"


def test_coerce_dict_variants():
    assert _coerce_dict({"entry_id": "e1"}) == {"entry_id": "e1"}
    assert _coerce_dict([{"entry_id": "e1"}, {"entry_id": "e2"}]) == {"entry_id": "e1"}
    assert _coerce_dict([]) == {"_raw": []}
    assert _coerce_dict("str-result") == {"_raw": "str-result"}
    assert _coerce_dict(None) == {}
