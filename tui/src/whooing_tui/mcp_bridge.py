"""후잉 공식 MCP 서버 (whooing.com/mcp) 호출 bridge — Phase 4 scaffolding.

후잉 REST API 가 노출하지 않는 영역 (보고서·예산·BBS·자주입력·매월입력
등) 을 다룰 때 사용. 같은 monorepo 의 `whooing-mcp-server-wrapper` 가 이미
`whooing_mcp.official_mcp.OfficialMcpClient` 로 HTTP JSON-RPC 호출을 구현
해 놨으므로 본 모듈은 그 클라이언트를 wrap 해 TUI 컨벤션 (`ToolError`
계층) 으로 결과를 변환한다.

**위치**: monorepo 안에서만 동작 — `whooing-mcp` 패키지가 같은 venv 에
editable install 되어 있어야 함. 외부 PyPI 배포 대상 아님 (Phase 4 미완성).

**사용**:
    bridge = WhooingMcpBridge(token=auth.token)
    tools = await bridge.list_tools()                 # 공식 MCP 도구 목록
    result = await bridge.call("report-get", {...})   # 도구 호출

호출자 (HomeScreen / 별도 화면) 가 이 결과를 표시. UI 통합은 후속 CL —
본 CL 은 thin bridge + 단위 테스트만.
"""

from __future__ import annotations

import logging
from typing import Any

from whooing_tui.models import ToolError

log = logging.getLogger(__name__)


class WhooingMcpBridge:
    """공식 후잉 MCP 호출 wrapper.

    내부적으로 `whooing_mcp.official_mcp.OfficialMcpClient` (mcp 패키지) 를
    사용. mcp 패키지가 install 안 돼 있으면 `__init__` 에서 ImportError 가
    `ToolError("INTERNAL", ...)` 로 변환된다 — 호출자가 그래픽으로 안내.
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://whooing.com/mcp",
        timeout: float = 30.0,
    ) -> None:
        try:
            # 지연 import — monorepo 외부 환경에선 import 자체가 실패할 수
            # 있어 호출 시점에 명확한 에러로 변환.
            from whooing_mcp.official_mcp import OfficialMcpClient
        except ImportError as e:
            raise ToolError(
                "INTERNAL",
                "whooing-mcp 패키지를 찾을 수 없습니다 — monorepo 안에서 "
                "`make install` 로 mcp/ 를 editable install 하세요.",
                cause=str(e),
            ) from e
        self._inner = OfficialMcpClient(token, base_url=base_url, timeout=timeout)

    async def list_tools(self) -> list[dict[str, Any]]:
        """공식 MCP 의 도구 목록 (`tools/list`)."""
        try:
            return await self._inner.list_tools()
        except Exception as e:
            raise self._to_tool_error(e, "tools/list 실패")

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """단일 도구 호출. 결과는 dict — 도구별 schema 는 호출자가 해석."""
        try:
            return await self._inner.call_tool(name, arguments)
        except Exception as e:
            raise self._to_tool_error(e, f"tools/call({name}) 실패")

    @staticmethod
    def _to_tool_error(e: Exception, prefix: str) -> ToolError:
        """공식 MCP 의 `OfficialMcpError` 를 ToolError 로 매핑.

        외부 모듈을 import 안 하기 위해 type name 으로 분기. JSON-RPC error
        의 code/data 는 ToolError details 에 보존.
        """
        cls_name = type(e).__name__
        if cls_name == "OfficialMcpError":
            code = getattr(e, "code", None)
            data = getattr(e, "data", None)
            # 공식 MCP 의 4xx 류는 사용자 가시 USER_INPUT, 그 외는 UPSTREAM.
            kind = "USER_INPUT" if isinstance(code, int) and -32700 <= code <= -32600 else "UPSTREAM"
            return ToolError(kind, f"{prefix}: {e}", code=code, data=data)
        # network / timeout
        return ToolError("UPSTREAM", f"{prefix}: {cls_name}: {e}")
