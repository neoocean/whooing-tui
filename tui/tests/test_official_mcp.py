"""OfficialMcpClient 단위 테스트 — JSON-RPC envelope + 응답 unwrap.

라이브 후잉 MCP 는 안 두드리고 respx 로 HTTP 응답 모킹.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from whooing_tui.official_mcp import (
    OfficialMcpClient,
    OfficialMcpError,
    _parse_first_sse_data,
)


# ---- SSE parser ------------------------------------------------------


def test_parse_first_sse_data_simple():
    text = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {"ok": true}}\n\n'
    p = _parse_first_sse_data(text)
    assert p["result"]["ok"] is True


def test_parse_first_sse_data_skips_event_line():
    text = "event: x\ndata: {\"a\":1}\n"
    p = _parse_first_sse_data(text)
    assert p == {"a": 1}


def test_parse_first_sse_data_no_data_raises():
    with pytest.raises(OfficialMcpError):
        _parse_first_sse_data("event: ping\n\n")


def test_parse_first_sse_data_bad_json_raises():
    with pytest.raises(OfficialMcpError):
        _parse_first_sse_data("data: not-json\n")


# ---- call_tool ------------------------------------------------------


@pytest.mark.asyncio
async def test_call_tool_returns_structured_content():
    """structuredContent 가 있으면 그것 우선 반환."""
    om = OfficialMcpClient(token="x__test")
    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(
            return_value=httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "result": {
                    "structuredContent": {"total": 42, "items": ["a", "b"]},
                    "content": [{"type": "text", "text": "ignored"}],
                },
            }),
        )
        out = await om.call_tool("test-tool", {"x": 1})
    assert out == {"total": 42, "items": ["a", "b"]}


@pytest.mark.asyncio
async def test_call_tool_falls_back_to_text_content_json_parse():
    """structuredContent 없으면 text content 파싱 시도."""
    om = OfficialMcpClient(token="x__test")
    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(
            return_value=httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "result": {
                    "content": [{"type": "text", "text": '{"y": 99}'}],
                },
            }),
        )
        out = await om.call_tool("test-tool", {})
    assert out == {"y": 99}


@pytest.mark.asyncio
async def test_call_tool_text_content_not_json_returns_raw():
    """text content 가 JSON 아니면 raw string."""
    om = OfficialMcpClient(token="x__test")
    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(
            return_value=httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "result": {
                    "content": [{"type": "text", "text": "plain hello"}],
                },
            }),
        )
        out = await om.call_tool("t", {})
    assert out == "plain hello"


@pytest.mark.asyncio
async def test_call_tool_is_error_raises_official_mcp_error():
    """isError=True 응답 → OfficialMcpError, text content 합쳐 메시지로."""
    om = OfficialMcpClient(token="x__test")
    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(
            return_value=httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "section_id required"}],
                },
            }),
        )
        with pytest.raises(OfficialMcpError, match="section_id required"):
            await om.call_tool("test-tool", {})


@pytest.mark.asyncio
async def test_call_tool_jsonrpc_error_raises():
    """JSON-RPC error 응답 → OfficialMcpError (code + message)."""
    om = OfficialMcpClient(token="x__test")
    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(
            return_value=httpx.Response(200, json={
                "jsonrpc": "2.0", "id": 1,
                "error": {"code": -32602, "message": "Invalid params"},
            }),
        )
        with pytest.raises(OfficialMcpError) as exc_info:
            await om.call_tool("test-tool", {})
        assert exc_info.value.code == -32602
        assert "Invalid params" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_tool_passes_x_api_key_header():
    """토큰이 X-API-Key 헤더로 전달돼야 후잉이 인증."""
    om = OfficialMcpClient(token="x__secret-token")
    captured = {}

    def _check(request):
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": 1,
            "result": {"structuredContent": {}},
        })

    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(side_effect=_check)
        await om.call_tool("t", {})

    assert captured["headers"].get("x-api-key") == "x__secret-token"


@pytest.mark.asyncio
async def test_call_tool_request_envelope_is_jsonrpc_tools_call():
    """request 가 JSON-RPC 2.0 spec — method='tools/call' + params 그대로."""
    om = OfficialMcpClient(token="x__test")
    captured = {}

    def _check(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": 1,
            "result": {"structuredContent": {"ok": True}},
        })

    with respx.mock(base_url="https://whooing.com") as r:
        r.post("/mcp").mock(side_effect=_check)
        await om.call_tool("report-get", {"type": "report", "section_id": "s1"})

    body = captured["body"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "report-get"
    assert body["params"]["arguments"] == {"type": "report", "section_id": "s1"}


# ---- 감사 2026-06 §3-A: 에러 data sanitize ----------------------------


def test_official_mcp_error_masks_secret_in_data():
    from whooing_tui.official_mcp import OfficialMcpError
    e = OfficialMcpError(
        "boom",
        data={"webhook_token": "s3cr3t", "nested": {"token": "abc"}, "ok": 1},
    )
    assert e.data["webhook_token"] == "***masked***"
    assert e.data["nested"]["token"] == "***masked***"
    assert e.data["ok"] == 1          # 비밀 아닌 값은 보존.


def test_official_mcp_error_none_data_stays_none():
    from whooing_tui.official_mcp import OfficialMcpError
    assert OfficialMcpError("x").data is None
