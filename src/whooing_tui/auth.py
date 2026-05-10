"""AI 연동 토큰 헤더 빌더 + 마스크.

후잉 공식 인증은 `X-API-Key: __eyJh...` 단일 헤더이다. 토큰 자체는 절대
로깅에 노출되어선 안 되며, 본 모듈의 객체는 `__repr__` / `__str__` 모두
마스크된 형태만 반환한다.

whooing-mcp-server-wrapper 의 동일 모듈에서 인용. TUI 와 MCP 서버가 같은
규칙으로 토큰을 다루도록 의도된 코드 중복.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WhooingAuth:
    """AI 연동 토큰 1개를 보관하고 HTTP 헤더를 빌드한다."""

    token: str

    def headers(self) -> dict[str, str]:
        """X-API-Key 단일 헤더. 후잉 공식 권장 (whooing.com/api/docs)."""
        return {"X-API-Key": self.token}

    def __repr__(self) -> str:
        # __eyJh... 같은 토큰의 마지막 4자리만 hint 로 남기고 나머지는 마스크.
        # 토큰이 너무 짧으면 hint 도 안 남긴다 (오용 방지).
        if len(self.token) > 12:
            return f"WhooingAuth(token=***{self.token[-4:]}, len={len(self.token)})"
        return "WhooingAuth(token=***)"

    __str__ = __repr__


def load_auth_from_env() -> WhooingAuth:
    """`.env` / 셸 환경변수의 WHOOING_AI_TOKEN 으로 WhooingAuth 생성.

    `.env` 가 있으면 자동 로드 (python-dotenv). 토큰이 비어있거나 placeholder
    (`__eyJh...`) 면 ValueError. 호출자(CLI/TUI) 가 적절한 에러 메시지로 변환.
    """
    import os

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover — dotenv 는 base 의존성
        pass

    token = (os.getenv("WHOOING_AI_TOKEN") or "").strip()
    if not token:
        raise ValueError(
            "WHOOING_AI_TOKEN 미설정. .env 에 후잉 AI 연동 토큰을 적거나 "
            "셸 환경변수로 export 하세요. 토큰 발급: 후잉 → 사용자 > 계정 > "
            "비밀번호 및 보안 > AI 토큰 발급."
        )
    if token == "__eyJh..." or token.startswith("__eyJh...") and len(token) < 32:
        raise ValueError(
            "WHOOING_AI_TOKEN 이 .env.example placeholder 그대로입니다. "
            "실 토큰으로 교체하세요."
        )
    return WhooingAuth(token=token)
