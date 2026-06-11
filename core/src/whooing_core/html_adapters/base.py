"""HTML adapter 공통 — Playwright 헤드리스 복호화 + DOM 추출 helper."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class HTMLDetectResult:
    detected_issuer: str | None
    confidence: float
    head_excerpt: str


class HtmlDecryptError(Exception):
    """복호화 실패 — 잘못된 password / 형식 변경 / Playwright 미설치 등."""


async def decrypt_html_with_playwright(
    html_path: str,
    password: str,
    *,
    password_input_selector: str = "#password",
    submit_function: str = "UserFunc()",
    wait_after_submit_ms: int = 2000,
    expected_alert_substrings: list[str] | None = None,
    prefill_js: str | None = None,
) -> str:
    """JS 가 client-side 복호화하는 HTML 을 헤드리스 Chromium 으로 처리.

    Args:
      prefill_js: 패스워드 fill **전에** 실행할 JS (선택). 예: hyundai vestmail
        은 `#password` 가 `display:none` 으로 시작하므로 visible 처리 필요.

    Returns:
      복호화 후 page.content() (전체 평문 HTML)

    Raises:
      HtmlDecryptError: 패스워드 틀림 (alert 발생) 또는 Playwright 실패.
    """
    try:
        from playwright.async_api import async_playwright  # lazy import
    except ImportError as ex:
        raise HtmlDecryptError(
            "Playwright 미설치. `pip install playwright && playwright install chromium`"
        ) from ex

    file_url = f"file://{Path(html_path).resolve()}"
    log.info("decrypting %s", html_path)

    expected_alerts = expected_alert_substrings or [
        "비밀번호가 일치하지 않습니다",
        "암호가 틀렸",
        "incorrect",
    ]
    captured_alerts: list[str] = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()

            # 감사 2026-06 §2-A: 명세서 HTML 은 이메일 수신 = 공격자 제어
            # 가능. 사용자가 방금 입력한 명세서 암호를 악성 JS 가 원격
            # beacon 하지 못하도록, `file:` 외 모든 요청을 차단(오프라인).
            async def _block_remote(route):
                url = route.request.url or ""
                if url.startswith("file:") or url.startswith("data:") \
                        or url.startswith("about:"):
                    await route.continue_()
                else:
                    await route.abort()
            await context.route("**/*", _block_remote)

            page = await context.new_page()

            async def on_dialog(dialog):
                captured_alerts.append(dialog.message)
                await dialog.dismiss()
            page.on("dialog", on_dialog)

            await page.goto(file_url, wait_until="load")
            if prefill_js:
                await page.evaluate(prefill_js)
            await page.fill(password_input_selector, password)
            await page.evaluate(submit_function)
            await page.wait_for_timeout(wait_after_submit_ms)

            # 패스워드 틀림 detection
            for alert_msg in captured_alerts:
                if any(s in alert_msg for s in expected_alerts):
                    raise HtmlDecryptError(
                        f"password rejected: {alert_msg!r} "
                        f"(captured alerts: {captured_alerts})"
                    )

            decrypted = await page.content()
            await browser.close()
            return decrypted
    except HtmlDecryptError:
        raise
    except Exception as ex:
        # CL #52841+: Playwright Python 패키지는 설치돼있어도 *브라우저
        # 바이너리* 가 별도 다운로드 필요. 자주 발생하는 케이스라 사용자에게
        # 정확한 fix 명령을 표면화 — "Executable doesn't exist" / "playwright
        # install" 메시지가 보이면 안내 cmd 를 prefix.
        msg = str(ex)
        if (
            "Executable doesn't exist" in msg
            or "playwright install" in msg
        ):
            raise HtmlDecryptError(
                "Playwright 브라우저 바이너리 미설치 — 터미널에서 다음 명령을 "
                "한 번 실행하세요:\n\n"
                "    .venv/bin/playwright install chromium\n\n"
                "(또는 `make install` 을 다시 실행 — CL #52841+ 부터 자동 포함). "
                f"\n\n원인 메시지: {ex}",
            ) from ex
        raise HtmlDecryptError(f"Playwright 복호화 실패: {ex}") from ex
