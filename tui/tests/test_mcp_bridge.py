"""WhooingMcpBridge — 공식 후잉 MCP 호출 wrapper 단위 테스트.

mcp 패키지의 `OfficialMcpClient` 자체는 mcp 측 테스트가 커버. 본 파일은
TUI 의 bridge layer 만 — error 매핑 + ImportError 처리 + 정상 위임.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from whooing_tui import mcp_bridge as mb
from whooing_tui.mcp_bridge import WhooingMcpBridge
from whooing_tui.models import ToolError


# ---- helpers -----------------------------------------------------------


class _FakeOfficialClient:
    """`whooing_mcp.official_mcp.OfficialMcpClient` 흉내 — 호출 인자 기록."""

    def __init__(self, token: str, base_url: str, timeout: float) -> None:
        self.token = token
        self.base_url = base_url
        self.timeout = timeout
        self.list_tools_calls = 0
        self.call_tool_calls: list[tuple[str, dict[str, Any]]] = []
        self.tools_result: list[dict[str, Any]] = []
        self.call_result: Any = {}
        self.tools_error: Exception | None = None
        self.call_error: Exception | None = None

    async def list_tools(self) -> list[dict[str, Any]]:
        self.list_tools_calls += 1
        if self.tools_error is not None:
            raise self.tools_error
        return self.tools_result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.call_tool_calls.append((name, arguments))
        if self.call_error is not None:
            raise self.call_error
        return self.call_result


class _FakeOfficialMcpError(Exception):
    """OfficialMcpError 시뮬 — type name 으로 분기되므로 본 클래스 이름 일치."""

    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        self.code = code
        self.data = data
        super().__init__(message)


# 본 클래스의 type name 을 "OfficialMcpError" 로 만들어 _to_tool_error 의
# 분기를 자극. 단순한 attribute 변경은 안 되니 새 class 로.
class OfficialMcpError(_FakeOfficialMcpError):
    pass


@pytest.fixture
def patched_official_client(monkeypatch):
    """`whooing_mcp.official_mcp.OfficialMcpClient` 를 fake 로 교체.

    bridge 가 import 시점에 try/except 한 번만 하므로 monkeypatch 가 modules
    에 적용돼야 import 가 fake 를 가져온다.
    """
    fake_module = types.ModuleType("whooing_mcp.official_mcp")
    fake_module.OfficialMcpClient = _FakeOfficialClient
    fake_module.OfficialMcpError = OfficialMcpError
    fake_pkg = types.ModuleType("whooing_mcp")
    fake_pkg.official_mcp = fake_module
    monkeypatch.setitem(sys.modules, "whooing_mcp", fake_pkg)
    monkeypatch.setitem(sys.modules, "whooing_mcp.official_mcp", fake_module)
    return _FakeOfficialClient


# ---- ImportError → ToolError(INTERNAL) ---------------------------------


def test_bridge_init_missing_mcp_raises_internal_tool_error(monkeypatch):
    """whooing_mcp 가 없으면 ToolError(INTERNAL) 로 변환."""
    # 두 entry 모두 None 으로 두고, 진짜 import 가 None.attr 에서 실패하도록
    monkeypatch.setitem(sys.modules, "whooing_mcp.official_mcp", None)
    with pytest.raises(ToolError) as ei:
        WhooingMcpBridge(token="__eyJhfaketokenfortests1234")
    assert ei.value.kind == "INTERNAL"
    assert "whooing-mcp" in ei.value.message


# ---- 정상 위임 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_delegates_to_inner(patched_official_client):
    bridge = WhooingMcpBridge(token="__tok123")
    inner = bridge._inner  # type: ignore[attr-defined]
    inner.tools_result = [{"name": "report-get"}, {"name": "budget-get"}]
    out = await bridge.list_tools()
    assert out == [{"name": "report-get"}, {"name": "budget-get"}]
    assert inner.list_tools_calls == 1


@pytest.mark.asyncio
async def test_call_delegates_with_args(patched_official_client):
    bridge = WhooingMcpBridge(token="__tok123")
    inner = bridge._inner  # type: ignore[attr-defined]
    inner.call_result = {"value": 42}
    out = await bridge.call("report-get", {"section_id": "s1", "month": "202605"})
    assert out == {"value": 42}
    assert inner.call_tool_calls == [
        ("report-get", {"section_id": "s1", "month": "202605"}),
    ]


# ---- error 매핑 -------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tools_official_error_maps_to_upstream(patched_official_client):
    bridge = WhooingMcpBridge(token="__tok123")
    inner = bridge._inner  # type: ignore[attr-defined]
    inner.tools_error = OfficialMcpError("server down", code=-32000, data={"info": "x"})
    with pytest.raises(ToolError) as ei:
        await bridge.list_tools()
    # -32000 은 implementation-defined → UPSTREAM
    assert ei.value.kind == "UPSTREAM"
    assert ei.value.details.get("code") == -32000
    assert ei.value.details.get("data") == {"info": "x"}


@pytest.mark.asyncio
async def test_call_official_error_jsonrpc_invalid_request_is_user_input(
    patched_official_client,
):
    """JSON-RPC -32600 (Invalid Request) 같은 4xx 류는 USER_INPUT."""
    bridge = WhooingMcpBridge(token="__tok123")
    inner = bridge._inner  # type: ignore[attr-defined]
    inner.call_error = OfficialMcpError("invalid params", code=-32600)
    with pytest.raises(ToolError) as ei:
        await bridge.call("report-get", {})
    assert ei.value.kind == "USER_INPUT"


@pytest.mark.asyncio
async def test_call_network_error_maps_to_upstream(patched_official_client):
    """OfficialMcpError 가 아닌 일반 예외 (timeout 등) → UPSTREAM."""
    bridge = WhooingMcpBridge(token="__tok123")
    inner = bridge._inner  # type: ignore[attr-defined]
    inner.call_error = TimeoutError("read timeout")
    with pytest.raises(ToolError) as ei:
        await bridge.call("report-get", {})
    assert ei.value.kind == "UPSTREAM"
    assert "TimeoutError" in ei.value.message
