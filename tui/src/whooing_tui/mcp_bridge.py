"""후잉 공식 MCP 서버 (whooing.com/mcp) 호출 bridge — 자체 HTTP JSON-RPC 클라이언트.

후잉 REST API 가 노출하지 않는 영역 (보고서·예산·BBS·자주입력·매월입력
등) 을 다룰 때 사용. 본 모듈은 자체적으로 HTTP JSON-RPC envelope 을 만들어
호출 — 외부 패키지 의존 없음 (httpx 만 사용, 본 패키지의 base 의존성).

> **이력**: 본래 archived `whooing-mcp-server-wrapper` 의 `OfficialMcpClient`
> 를 wrap 하던 모듈이었으나 (CL #50990 scaffolding), wrapper 종료 후
> archived 패키지에 대한 잔재 의존을 끊기 위해 자체 클라이언트로 재작성
> (CL #51003).

> **현재 상태**: TUI 의 어떤 화면에서도 호출하지 않는 라이브러리 layer —
> Phase 4 (보고서 / 예산 / 자주입력 매칭 등) 의 미구현 후보. UI 통합은
> 별도 CL 에서 진행 예정.

**사용**:

    bridge = WhooingMcpBridge(token=auth.token)
    tools = await bridge.list_tools()                 # 공식 MCP 도구 목록
    result = await bridge.call("report-get", {...})   # 도구 호출

**동작**:

- Endpoint: POST `https://whooing.com/mcp` (default).
- Headers: `X-API-Key` + `Content-Type: application/json` +
  `Accept: application/json, text/event-stream` (MCP spec).
- Body envelope: `{jsonrpc: "2.0", id: <N>, method: "tools/list" |
  "tools/call", params: {...}}`.
- Response: JSON-RPC `{result: ...}` 또는 `{error: {message, code, data}}`.
- `tools/call` 결과의 `isError: True` 인 경우는 호출자가 보지 못하도록
  `ToolError("UPSTREAM", ...)` 으로 변환.

**호출자 컨벤션**:

- 모든 예외는 `ToolError` (TUI 컨벤션) 로 변환:
  * JSON-RPC `error.code` 가 -32600 (Invalid Request) 같은 4xx 류 → USER_INPUT.
  * 그 외 / network / non-JSON 응답 → UPSTREAM.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from whooing_tui.models import ToolError

log = logging.getLogger(__name__)

DEFAULT_OFFICIAL_MCP_URL = "https://whooing.com/mcp"


class WhooingMcpBridge:
    """후잉 공식 MCP 호출 client — 자체 HTTP JSON-RPC.

    인스턴스는 stateless 한 connection 풀이 아닌 1회성 wrapper. 매 호출
    마다 `httpx.AsyncClient` 를 새로 만든다 (`WhooingClient` 와 같은 패턴).
    """

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_OFFICIAL_MCP_URL,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._timeout = timeout
        self._req_id = 0

    # ---- public ------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """공식 MCP 가 노출한 도구 목록 (`tools/list`)."""
        result = await self._call("tools/list", {})
        return result.get("tools", []) or []

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """단일 도구 호출 (`tools/call`).

        반환은 MCP spec 의 tool result dict (`{content: [...], isError?,
        structuredContent?}`). `isError=True` 인 경우 ToolError(UPSTREAM)
        로 raise.
        """
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            content = result.get("content") or []
            msg_parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            raise ToolError(
                "UPSTREAM",
                f"tool {name!r} 가 isError 반환: "
                f"{' | '.join(msg_parts) or '(메시지 없음)'}",
                tool_name=name,
                tool_result=result,
            )
        return result

    # ---- internal ----------------------------------------------------

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """JSON-RPC POST. 응답의 `result` 만 반환 — error / non-JSON 은 ToolError."""
        rpc_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": method,
            "params": params,
        }
        log.debug("MCP %s %s id=%d", self._base_url, method, rpc_id)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(self._base_url, headers=self._headers(), json=payload)
        except httpx.HTTPError as e:
            raise ToolError(
                "UPSTREAM",
                f"공식 MCP 호출 실패 ({method}): {type(e).__name__}: {e}",
            )

        try:
            body = r.json()
        except Exception:
            raise ToolError(
                "UPSTREAM",
                f"비-JSON 응답 from 공식 MCP (status={r.status_code})",
                status=r.status_code,
                snippet=r.text[:200],
            )

        if "error" in body:
            err = body["error"] or {}
            code = err.get("code")
            # JSON-RPC 표준 에러 (-32700~-32600) 는 USER_INPUT, 그 외는 UPSTREAM.
            kind = (
                "USER_INPUT"
                if isinstance(code, int) and -32700 <= code <= -32600
                else "UPSTREAM"
            )
            raise ToolError(
                kind,
                f"공식 MCP error ({method}): {err.get('message', '(no message)')}",
                code=code,
                data=err.get("data"),
            )

        result = body.get("result")
        if result is None:
            raise ToolError(
                "UPSTREAM",
                f"공식 MCP 응답에 'result' 없음 ({method}): {body!r:.200}",
            )
        return result
