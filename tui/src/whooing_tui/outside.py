"""후잉 외부입력(임시저장소) 접근 — 조회 · 확정(입력) · 삭제.

후잉 "외부입력" = 카드/은행 SMS 가 웹훅/이메일로 들어와 파싱됐지만 아직 장부에
*확정되지 않은* 항목(=임시저장소). 공식 OpenAPI 는 이걸 **쓰기 전용**으로만
노출(`POST entries/outside.json` 제출)하고 **조회 GET 은 없다**. 후잉 웹 UI
(`main_insert_outside.js`)가 내부적으로 쓰는 **오버로드된 호출**로 목록을 읽을
수 있고 X-API-Key 인증이 통함을 확인(2026-06-14). 자세한 워크플로/엔드포인트는
[`docs/scenarios/14-external-input-staging.md`].

본 모듈은 `official_mcp.py` 처럼 **standalone httpx 클라이언트**(client.py 비의존)
로, `WhooingAuth` 만 받아 동작한다.

⚠️ 비공식·내부 엔드포인트라 후잉이 예고 없이 바꿀 수 있다 — 호출부는 실패를
graceful 하게 처리할 것.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from whooing_tui.auth import WhooingAuth

log = logging.getLogger(__name__)

DEFAULT_BASE = "https://whooing.com/api"
DEFAULT_TIMEOUT = 20.0

# 한 번에 받는 임시저장소 페이지 크기(웹 UI 와 동일하게 ~100 가정).
PAGE_SAFE_MAX = 100


class OutsideError(Exception):
    """외부입력 접근 실패 — 인증/파라미터/내부 엔드포인트 변경 등."""


def parse_counter_account(r: str) -> tuple[str, str]:
    """임시저장소 행의 `r` (예: ``"liabilities_x80"``) → (account, account_id).

    후잉이 추정한 상대계정. 형식이 어긋나면 ("", "").
    """
    if not r or "_" not in r:
        return "", ""
    account, _, account_id = r.rpartition("_")
    return account, account_id


def staged_item_text(row: dict[str, Any]) -> str:
    """임시저장소 행의 사람이 읽을 적요 후보 — detail > r3(가맹점) > raw."""
    for key in ("detail", "r3", "item", "raw"):
        v = (row.get(key) or "").strip()
        if v:
            return v
    return ""


def build_entry(
    row: dict[str, Any],
    *,
    l_account: str,
    l_account_id: str,
    item: str | None = None,
    memo: str = "",
) -> dict[str, Any]:
    """임시저장소 행 + 사용자가 고른 차변 계정 → 확정용 entry dict.

    후잉이 추정한 상대계정(`r`)을 대변(right)으로, 사용자가 고른 계정을
    차변(left)으로 둔다(카드 지출의 일반형: 차변=지출항목, 대변=카드부채).
    `entries.json` 의 `entries[]` 한 원소 형태(웹 UI 직렬화와 동일 키).
    """
    r_account, r_account_id = parse_counter_account(row.get("r", ""))
    return {
        "out_id": str(row.get("out_id", "")),
        "entry_date": str(row.get("entry_date", "")),
        "item": item if item is not None else staged_item_text(row),
        "money": row.get("money", 0),
        "memo": memo,
        "l_account": l_account,
        "l_account_id": l_account_id,
        "r_account": r_account,
        "r_account_id": r_account_id,
    }


class OutsideClient:
    """후잉 외부입력(임시저장소) 접근 클라이언트 (standalone)."""

    def __init__(
        self,
        auth: WhooingAuth,
        *,
        base_url: str = DEFAULT_BASE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ---- low-level ------------------------------------------------------

    async def _request(
        self, method: str, path: str, *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as cli:
                resp = await cli.request(
                    method, url,
                    headers=self.auth.headers(),
                    data=data,      # form-urlencoded (후잉 mutation 규약)
                    params=params,
                )
        except httpx.HTTPError as ex:
            raise OutsideError(f"외부입력 요청 실패(network): {ex}") from ex

        try:
            body = resp.json()
        except Exception as ex:  # noqa: BLE001 — 비-JSON 응답 = 내부 변경 신호
            raise OutsideError(
                f"외부입력 응답 파싱 실패(내부 엔드포인트 변경 가능): "
                f"HTTP {resp.status_code}"
            ) from ex

        code = body.get("code")
        if code != 200:
            raise OutsideError(
                f"외부입력 거부 (code={code}): {body.get('message') or ''}"
            )
        return body

    # ---- high-level -----------------------------------------------------

    # 주의: 웹 UI 의 개수 배지(`GET /api/main/outside_count`)는 `/api/main/*`
    # = 세션 전용 네임스페이스라 X-API-Key 로는 403 이다(2026-06-14 확인).
    # 그래서 별도 count 엔드포인트는 쓰지 않고 `list_all` 길이로 대신한다.

    async def list(
        self, section_id: str, *, omax_id: str = "",
    ) -> list[dict[str, Any]]:
        """임시저장소 한 페이지 조회.

        오버로드 POST: `rows=""` (빈 값 = 읽기). `omax_id` 에 직전 페이지의
        마지막 `out_id` 를 넣어 다음 페이지를 받는다(없으면 첫 페이지).
        """
        body = await self._request(
            "POST", "/entries/outside.json",
            data={
                "section_id": section_id,
                "rows": "",          # 빈 값 = 조회(읽기)
                "ids": "out_id",
                "omax_id": omax_id,
                "m": "n",
            },
        )
        results = body.get("results") or {}
        outdata = results.get("outdata") or []
        return list(outdata) if isinstance(outdata, list) else []

    async def list_all(
        self, section_id: str, *, max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """임시저장소 전체를 페이지네이션으로 모아서 반환.

        `out_id` 정렬 기준 마지막 id 를 `omax_id` 로 넘기며 한 페이지(~100)씩.
        한 페이지가 PAGE_SAFE_MAX 미만이면 마지막 페이지로 보고 종료.
        """
        acc: list[dict[str, Any]] = []
        seen: set[str] = set()
        omax = ""
        for _ in range(max_pages):
            page = await self.list(section_id, omax_id=omax)
            fresh = [r for r in page if str(r.get("out_id")) not in seen]
            for r in fresh:
                seen.add(str(r.get("out_id")))
            acc.extend(fresh)
            if len(page) < PAGE_SAFE_MAX or not fresh:
                break
            omax = str(page[-1].get("out_id") or "")
            if not omax:
                break
        return acc

    async def confirm(
        self,
        section_id: str,
        entries: list[dict[str, Any]],
        del_ids: list[str],
    ) -> dict[str, Any]:
        """임시저장소 항목을 장부에 **확정 입력** + 해당 out_id 제거(원자적).

        웹 UI 와 동일하게 `entries.json` 에 `entries`(JSON 배열) + `del_ids`
        (콤마 결합 out_id)를 보낸다.
        """
        body = await self._request(
            "POST", "/entries.json",
            data={
                "section_id": section_id,
                "entries": json.dumps(entries, ensure_ascii=False),
                "del_ids": ",".join(str(d) for d in del_ids),
            },
        )
        return body.get("results") or {}

    async def delete(
        self, section_id: str, del_ids: list[str],
    ) -> dict[str, Any]:
        """임시저장소 항목을 장부 입력 없이 **삭제(제거)**.

        `entries.json` 에 빈 `entries` + `del_ids` 만 보낸다(생성 없음).
        """
        return await self.confirm(section_id, [], del_ids)

    async def empty(self, section_id: str) -> dict[str, Any]:
        """임시저장소 **전체 비우기** (되돌릴 수 없음 — 호출부 확인 필수)."""
        body = await self._request(
            "POST", "/entries/empty_outside.json",
            data={"section_id": section_id},
        )
        return body.get("results") or {}
