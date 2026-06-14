"""후잉 서버 첨부 → whooing-tui 로컬 첨부 가져오기.

후잉 거래의 `attachments[]`(entries-list/report-get 응답)는 서버에 저장된 첨부의
메타다 — `{uuid, src, filename, mimeType, size}`. `src`(`https://static.whooing.
com/get/<uuid>`)는 uuid 자체가 capability 토큰이라 인증 없이도 받아진다(있어도
무방). 받은 바이트를 로컬 첨부 시스템(`attachment_browser.add_attachment`)으로
저장하면 sqlite + `attachment/` + sha256 dedup + P4 에 들어간다.

본 모듈은 다운로드/판별만 담당(standalone, httpx). 실제 로컬 저장은 호출부가
`add_attachment` 로 한다.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class ServerAttachmentError(Exception):
    """후잉 서버 첨부 다운로드 실패."""


async def download(
    src: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """후잉 서버 첨부 `src` URL 을 받아 원본 바이트 반환.

    uuid capability 라 인증 불필요지만, `token` 이 있으면 X-API-Key 로 붙인다.
    """
    headers = {"X-API-Key": token} if token else {}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as cli:
            resp = await cli.get(src, headers=headers)
    except httpx.HTTPError as ex:
        raise ServerAttachmentError(f"다운로드 실패(network): {ex}") from ex
    if resp.status_code != 200:
        raise ServerAttachmentError(
            f"다운로드 실패 HTTP {resp.status_code}: {src}"
        )
    return resp.content


def pending_imports(
    server_attachments: list[dict[str, Any]] | None,
    local_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """아직 로컬에 없는(같은 filename 부재) 서버 첨부만 추린다.

    이미 가져온 건 같은 파일명이 로컬 `entry_attachments` 에 있으므로 제외 —
    재실행해도 중복 안 만든다(sha256 dedup 과 별개로 호출 자체를 줄임).
    `src` 가 없는 메타는 받을 수 없으니 제외.
    """
    have = {
        (r.get("original_filename") or "").strip()
        for r in (local_rows or [])
    }
    out: list[dict[str, Any]] = []
    for a in server_attachments or []:
        if not (a.get("src") or "").strip():
            continue
        fn = (a.get("filename") or "").strip()
        if fn and fn in have:
            continue
        out.append(a)
    return out
