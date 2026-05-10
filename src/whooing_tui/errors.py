"""HTTP → ToolError 매핑 + 로깅용 sanitizer.

후잉 응답 코드 (200/204/400/401/402/405/429/5xx) 를 사용자 가시 ToolError
로 변환. `/sections.json` 응답에 webhook_token (per-section secret) 이
포함되므로 디버그 로그에 누설되지 않도록 sanitize 도 함께 제공한다.

whooing-mcp-server-wrapper 의 동일 모듈에서 인용 — 같은 응답을 같은
규칙으로 해석하기 위한 의도적 중복.
"""

from __future__ import annotations

from typing import Any

from whooing_tui.models import ToolError

# 후잉 응답 / 객체에서 마스크해야 할 secret 키.
# /sections.json 응답에 webhook_token (per-section secret) 이 포함된다.
SECRET_KEYS: frozenset[str] = frozenset({
    "webhook_token",
    "token",
    "password",
    "api_key",
    "secret",
    "signature",
})


def map_response(
    code: int,
    message: str = "",
    body: dict[str, Any] | None = None,
    *,
    status: int | None = None,
) -> ToolError:
    """후잉 응답 코드를 사용자 가시 ToolError 로 변환.

      200 → caller 가 처리 (본 함수 호출 X — 정상 응답)
      204 → caller 가 빈 결과 처리 (본 함수 호출 X)
      400 → USER_INPUT
      401/405 → AUTH (token 만료/거부)
      402 → RATE_LIMIT (일일)
      429 → RATE_LIMIT (분당)
      5xx → UPSTREAM
      그 외 → UPSTREAM
    """
    body = body or {}
    rest = body.get("rest_of_api")
    msg = message or body.get("message") or ""

    if code in (401, 405):
        return ToolError(
            "AUTH",
            "AI 토큰이 만료되었거나 거부되었습니다. "
            "후잉 → 사용자 > 계정 > 비밀번호 및 보안 에서 재발급 후 .env 갱신.",
            upstream_message=msg,
            http_status=status,
        )
    if code == 402:
        return ToolError(
            "RATE_LIMIT",
            f"일일 한도 초과 (rest_of_api={rest}). 한국시간 자정에 리셋.",
            rest_of_api=rest,
            http_status=status,
        )
    if code == 429:
        return ToolError(
            "RATE_LIMIT",
            "분당 한도 초과 (1분 대기 후 재시도). client-side throttle 이 "
            "있어도 복수 인스턴스 동시 호출 시 발생 가능.",
            http_status=status,
        )
    if code == 400:
        return ToolError(
            "USER_INPUT",
            msg or "잘못된 파라미터",
            error_parameters=body.get("error_parameters") or {},
            http_status=status,
        )
    if 500 <= code < 600:
        return ToolError(
            "UPSTREAM",
            f"후잉 서버 오류 (code={code}): {msg}",
            http_status=status,
        )
    return ToolError(
        "UPSTREAM",
        f"예상치 못한 응답 code={code} message={msg!r}",
        body_keys=list(body.keys()) if isinstance(body, dict) else None,
        http_status=status,
    )


def sanitize_for_log(obj: Any) -> Any:
    """dict/list 안의 secret 값을 마스크. 원본 변형 없음.

    DEBUG 로그 / 픽스처 캡처 시 사용. SECRET_KEYS 매칭 키의 값을
    '***masked***' 로 치환.
    """
    if isinstance(obj, dict):
        return {
            k: "***masked***" if k.lower() in SECRET_KEYS else sanitize_for_log(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [sanitize_for_log(item) for item in obj]
    return obj


def sanitize_token(token: str) -> str:
    """토큰을 로그용으로 마스크. WhooingAuth.__repr__ 와 동일 규칙."""
    if not token:
        return "***empty***"
    if len(token) <= 12:
        return "***short***"
    return f"***{token[-4:]} (len={len(token)})"
