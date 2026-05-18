"""Minimal HTTP JSON-RPC client for the official Whooing MCP server.

CL #52755+. 후잉이 공식 MCP server (`https://whooing.com/mcp`) 를 운영
하고, 거기에는 정확히 정의된 도구 (report-get / budget-get / ...) +
schema 가 노출된다. 우리 client.py 의 REST path 추측 (`/report/{account}.json`
등) 이 일부 endpoint 에서 403 으로 실패했으므로 보고서 fetch 는 공식
MCP 위임이 더 안전.

본 모듈은 archived `mcp/src/whooing_mcp/official_mcp.py` 의 minimum
필요한 부분만 가져온 self-contained version — `mcp/` 패키지 import 의존
없음. JSON-RPC 2.0 spec 그대로.

사용:

    om = OfficialMcpClient(token)
    payload = await om.call_tool("report-get", {
        "type": "report", "section_id": "s9046", "account": "all",
        "rows_type": "none",
    })

`call_tool` 의 반환은 MCP spec 의 *content unwrap* 결과. 일반적으로
도구가 `structuredContent` 안에 dict 를 반환하면 그것; text 한 줄이면
파싱된 dict (가능 시) 또는 str.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_OFFICIAL_MCP_URL = "https://whooing.com/mcp"


class OfficialMcpError(Exception):
    """공식 MCP 호출 실패 — JSON-RPC error 또는 도구 isError 응답."""

    def __init__(
        self, message: str, *, code: int | None = None, data: Any = None,
    ) -> None:
        self.code = code
        self.data = data
        super().__init__(message)


class OfficialMcpClient:
    """tools/call 만 노출하는 최소 클라이언트."""

    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_OFFICIAL_MCP_URL,
        timeout: float = 30.0,
    ) -> None:
        self.token = token
        self.base_url = base_url
        self.timeout = timeout
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict[str, str]:
        return {
            "X-API-Key": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def call_tool(
        self, name: str, arguments: dict[str, Any],
    ) -> Any:
        """단일 도구 호출. JSON-RPC error / isError → `OfficialMcpError`.

        반환 값:
          - 도구가 `structuredContent` 반환 시 그 dict.
          - text content 만 있고 JSON parse 가능하면 dict/list.
          - 그 외엔 raw text 또는 content array.
        """
        result = await self._call(
            "tools/call", {"name": name, "arguments": arguments},
        )
        if result.get("isError"):
            text_parts: list[str] = []
            for c in result.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    text_parts.append(c.get("text", ""))
            raise OfficialMcpError(
                f"tool {name!r} returned isError: "
                f"{' | '.join(text_parts) or '(no message)'}",
                data=result,
            )
        # 1) structuredContent 우선.
        struct = result.get("structuredContent")
        if struct is not None:
            return struct
        # 2) content array — text 하나면 그것을 JSON parse 시도.
        content = result.get("content") or []
        if len(content) == 1 and isinstance(content[0], dict):
            c = content[0]
            if c.get("type") == "text":
                txt = c.get("text", "")
                try:
                    return json.loads(txt)
                except (ValueError, TypeError):
                    return txt
        return content

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            resp = await cli.post(
                self.base_url, headers=self._headers(), json=body,
            )
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            # SSE — 첫 `data: {...}` event 만 사용 (single-shot 응답).
            payload = _parse_first_sse_data(resp.text)
        else:
            try:
                payload = resp.json()
            except (ValueError, json.JSONDecodeError):
                raise OfficialMcpError(
                    f"non-JSON response (status={resp.status_code})",
                    code=resp.status_code,
                )
        if "error" in payload:
            err = payload["error"]
            raise OfficialMcpError(
                err.get("message", "(no message)"),
                code=err.get("code"),
                data=err.get("data"),
            )
        return payload.get("result", {})


def _parse_first_sse_data(text: str) -> dict[str, Any]:
    """text/event-stream 응답에서 첫 `data:` 라인의 JSON parse."""
    for line in text.splitlines():
        if line.startswith("data:"):
            data = line[len("data:"):].strip()
            if data:
                try:
                    return json.loads(data)
                except (ValueError, TypeError):
                    raise OfficialMcpError(
                        f"SSE data parse 실패: {data[:120]}",
                    )
    raise OfficialMcpError("SSE 응답에 data: 라인 없음")
