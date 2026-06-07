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
async def test_delete_entry_verifies_deletion_on_mcp_error():
    """CL #53110+: 공식 MCP 가 isError 를 반환해도 entry_date 로 재조회해
    실제 삭제됐으면 성공(idempotent) 처리.

    배경 (사용자 보고 2026-05-30): 65건 일괄 삭제 중 23건이 서버 부하로
    *삭제는 적용됐는데* isError("delete failed") 를 반환 → 과거엔 REST
    fallback 이 'section_id required' 로 가려 오삭제 실패 + 캐시 desync.
    """
    # MCP delete — isError 반환 (삭제는 됐다고 가정).
    respx.post("https://whooing.com/mcp").mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "delete failed"}],
                },
            },
        )
    )
    # 검증용 재조회 — 해당 일자에 e1 없음 (= 실제 삭제됨).
    list_route = respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"reports": [], "rows": [
                {"entry_id": "other", "entry_date": "20260101.0000", "money": 1},
            ]}},
        )
    )
    c = _client()
    out = await c.delete_entry(
        section_id="s1", entry_id="e1", entry_date="20260101",
    )
    assert out.get("verified_deleted") is True
    assert list_route.call_count >= 1


@respx.mock
async def test_delete_entry_raises_when_still_present():
    """MCP 오류 + 재조회 시 entry 가 여전히 존재 → DELETE_FAILED raise
    (실제 미삭제를 성공으로 오판하지 않음)."""
    respx.post("https://whooing.com/mcp").mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "delete failed"}],
                },
            },
        )
    )
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"reports": [], "rows": [
                {"entry_id": "e1", "entry_date": "20260101.0000", "money": 1},
            ]}},
        )
    )
    c = _client()
    with pytest.raises(ToolError) as ei:
        await c.delete_entry(
            section_id="s1", entry_id="e1", entry_date="20260101",
        )
    assert ei.value.kind == "DELETE_FAILED"


@respx.mock
async def test_delete_entry_mcp_error_without_date_raises():
    """entry_date 미제공 + MCP 오류 → 검증 불가 → DELETE_FAILED (REST
    fallback 으로 'section_id required' 마스킹하지 않음)."""
    respx.post("https://whooing.com/mcp").mock(
        return_value=Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32601, "message": "boom"},
            },
        )
    )
    c = _client()
    with pytest.raises(ToolError) as ei:
        await c.delete_entry(section_id="s1", entry_id="e1")
    assert ei.value.kind == "DELETE_FAILED"


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
