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


def _env_candidates() -> list["Path"]:
    """`.env` 탐색 후보 경로 — 우선순위 순.

    1. `$WHOOING_ENV` (절대 경로 override) — 명시적 강제.
    2. `~/.config/whooing/.env` — **공통 위치**. whooing-mcp-server-wrapper
       와 같은 위치를 공유하므로 토큰을 한 곳에 두면 양쪽 도구가 같이 사용.
    3. project root 의 `.env` (legacy) — backward compat.

    Returns 모든 후보 (존재 여부 무관). 호출자가 exists() 체크.
    """
    from pathlib import Path
    import os

    out: list[Path] = []
    explicit = os.getenv("WHOOING_ENV")
    if explicit:
        out.append(Path(explicit).expanduser())
    out.append(Path.home() / ".config" / "whooing" / ".env")
    # project root: tui/src/whooing_tui/auth.py → parents[3] (= monorepo root)
    try:
        out.append(Path(__file__).resolve().parents[3] / ".env")
    except IndexError:  # pragma: no cover — install layout 이 다를 때
        pass
    return out


def load_auth_from_env() -> WhooingAuth:
    """`.env` / 셸 환경변수의 WHOOING_AI_TOKEN 으로 WhooingAuth 생성.

    탐색 우선순위 (`override=False` — 셸 export 가 항상 최우선):
      1. 셸 환경변수 (이미 set 이면 그대로 사용)
      2. `$WHOOING_ENV` (절대 경로 override)
      3. `~/.config/whooing/.env` (공통 위치, whooing-mcp-server 와 공유)
      4. project root 의 `.env` (legacy)

    토큰이 비어있거나 placeholder (`__eyJh...`) 면 ValueError. 호출자
    (CLI/TUI) 가 적절한 에러 메시지로 변환.
    """
    import logging
    import os

    log = logging.getLogger(__name__)

    try:
        from dotenv import load_dotenv

        loaded_from: str | None = None
        for c in _env_candidates():
            if c.exists():
                load_dotenv(c, override=False)
                loaded_from = str(c)
                log.info("loaded .env from %s", c)
                break  # 첫 발견 1개만 (override=False 라 의미상 동등)
        if loaded_from is None:
            log.debug(
                ".env 후보 없음 — WHOOING_AI_TOKEN 은 셸 환경변수로만 들어와야 합니다.",
            )
    except ImportError:  # pragma: no cover — dotenv 는 base 의존성
        pass

    token = (os.getenv("WHOOING_AI_TOKEN") or "").strip()
    if not token:
        raise ValueError(
            "WHOOING_AI_TOKEN 미설정. .env 에 후잉 AI 연동 토큰을 적거나 "
            "셸 환경변수로 export 하세요. 권장 위치: ~/.config/whooing/.env "
            "(whooing-mcp-server-wrapper 와 공유). 토큰 발급: 후잉 → 사용자 "
            "> 계정 > 비밀번호 및 보안 > AI 토큰 발급."
        )
    if token == "__eyJh..." or token.startswith("__eyJh...") and len(token) < 32:
        raise ValueError(
            "WHOOING_AI_TOKEN 이 .env.example placeholder 그대로입니다. "
            "실 토큰으로 교체하세요."
        )
    return WhooingAuth(token=token)
