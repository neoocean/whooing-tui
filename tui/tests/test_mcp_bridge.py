"""WhooingMcpBridge — 자체 HTTP JSON-RPC 클라이언트 단위 테스트.

CL #51003 부터 본 모듈은 외부 패키지 의존 없이 httpx 만 사용. 테스트는
`respx` 로 후잉 공식 MCP 응답을 mock — 실 네트워크 없이 envelope / error
매핑 / `isError` 처리를 검증.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from whooing_tui.mcp_bridge import DEFAULT_OFFICIAL_MCP_URL, WhooingMcpBridge
from whooing_tui.models import ToolError


def _bridge() -> WhooingMcpBridge:
    return WhooingMcpBridge(
        token="__eyJhfaketokenfortests1234",
        base_url=DEFAULT_OFFICIAL_MCP_URL,
    )


def _rpc_ok(result: dict, rpc_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(code: int, message: str, *, data=None, rpc_id: int = 1) -> dict:
    err: dict = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": err}


# ---- list_tools --------------------------------------------------------


@respx.mock
async def test_list_tools_returns_tool_array():
    route = respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(
            200,
            json=_rpc_ok({"tools": [{"name": "report-get"}, {"name": "budget-get"}]}),
        )
    )
    bridge = _bridge()
    out = await bridge.list_tools()
    assert out == [{"name": "report-get"}, {"name": "budget-get"}]
    # request envelope 확인
    sent = json.loads(route.calls[0].request.read())
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "tools/list"
    assert sent["params"] == {}
    assert isinstance(sent["id"], int)
    # 헤더에 X-API-Key 가 있어야
    assert route.calls[0].request.headers["X-API-Key"].startswith("__eyJh")


@respx.mock
async def test_list_tools_missing_tools_key_returns_empty():
    """공식 MCP 가 `tools` key 없이 result 만 주면 빈 list."""
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(200, json=_rpc_ok({}))
    )
    bridge = _bridge()
    assert await bridge.list_tools() == []


# ---- call --------------------------------------------------------------


@respx.mock
async def test_call_passes_arguments_and_returns_result():
    route = respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(
            200,
            json=_rpc_ok({
                "content": [{"type": "text", "text": "보고서 내용"}],
                "structuredContent": {"sum": 12345},
            }),
        )
    )
    bridge = _bridge()
    out = await bridge.call("report-get", {"section_id": "s1", "month": "202605"})
    assert out["structuredContent"] == {"sum": 12345}
    sent = json.loads(route.calls[0].request.read())
    assert sent["method"] == "tools/call"
    assert sent["params"] == {
        "name": "report-get",
        "arguments": {"section_id": "s1", "month": "202605"},
    }


@respx.mock
async def test_call_isError_raises_upstream_with_text():
    """tool 결과의 `isError: True` → ToolError(UPSTREAM) + content text 추출."""
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(
            200,
            json=_rpc_ok({
                "content": [
                    {"type": "text", "text": "section_id required"},
                    {"type": "text", "text": "see api docs"},
                ],
                "isError": True,
            }),
        )
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.call("report-get", {})
    assert ei.value.kind == "UPSTREAM"
    assert "section_id required" in ei.value.message
    assert "see api docs" in ei.value.message
    assert ei.value.details.get("tool_name") == "report-get"


# ---- error 매핑 -------------------------------------------------------


@respx.mock
async def test_jsonrpc_invalid_request_maps_to_user_input():
    """JSON-RPC -32600 (Invalid Request) → USER_INPUT."""
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(200, json=_rpc_error(-32600, "Invalid Request"))
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.call("report-get", {})
    assert ei.value.kind == "USER_INPUT"
    assert ei.value.details.get("code") == -32600


@respx.mock
async def test_jsonrpc_method_not_found_maps_to_user_input():
    """-32601 (Method not found) 도 USER_INPUT 범위."""
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(200, json=_rpc_error(-32601, "Method not found"))
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.list_tools()
    assert ei.value.kind == "USER_INPUT"


@respx.mock
async def test_jsonrpc_implementation_defined_maps_to_upstream():
    """-32000 (implementation-defined) 같은 server 에러는 UPSTREAM."""
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(
            200,
            json=_rpc_error(-32000, "server unavailable", data={"info": "x"}),
        )
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.list_tools()
    assert ei.value.kind == "UPSTREAM"
    assert ei.value.details.get("code") == -32000
    assert ei.value.details.get("data") == {"info": "x"}


@respx.mock
async def test_non_json_response_maps_to_upstream():
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(502, text="<html>Bad Gateway</html>")
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.list_tools()
    assert ei.value.kind == "UPSTREAM"
    assert "비-JSON" in ei.value.message


@respx.mock
async def test_missing_result_maps_to_upstream():
    """result 도 error 도 없는 비정상 응답."""
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(200, json={"jsonrpc": "2.0", "id": 1})
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.list_tools()
    assert ei.value.kind == "UPSTREAM"


@respx.mock
async def test_network_error_maps_to_upstream():
    """httpx 가 raise 하는 transport 에러도 UPSTREAM."""
    import httpx as _httpx
    respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        side_effect=_httpx.ConnectError("connection refused"),
    )
    bridge = _bridge()
    with pytest.raises(ToolError) as ei:
        await bridge.list_tools()
    assert ei.value.kind == "UPSTREAM"
    assert "ConnectError" in ei.value.message


# ---- envelope 미세 사항 ------------------------------------------------


@respx.mock
async def test_request_id_increments_per_call():
    """동일 인스턴스의 연속 호출은 id 가 1씩 증가."""
    route = respx.post(DEFAULT_OFFICIAL_MCP_URL).mock(
        return_value=Response(200, json=_rpc_ok({"tools": []}))
    )
    bridge = _bridge()
    await bridge.list_tools()
    await bridge.list_tools()
    await bridge.list_tools()
    ids = [json.loads(c.request.read())["id"] for c in route.calls]
    assert ids == [1, 2, 3]


def test_no_external_whooing_mcp_dependency():
    """본 모듈이 archived `whooing_mcp` 패키지를 import 하지 않는지.

    CL #51003 의 핵심 — 자체 HTTP JSON-RPC 로 재작성하면서 archived
    의존을 끊었다. 회귀 방지.
    """
    import whooing_tui.mcp_bridge as _mb
    src = open(_mb.__file__, encoding="utf-8").read()
    assert "from whooing_mcp" not in src
    assert "import whooing_mcp" not in src
