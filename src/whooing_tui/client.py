"""후잉 REST API 클라이언트 — TUI 가 직접 사용.

읽기/쓰기 모두 후잉 공식 REST API 를 호출한다. 공식 MCP 서버를 거치지
않으므로 GET 외 POST/PUT/DELETE 도 지원한다 (1단계는 GET 위주, 거래
입력 등 mutating endpoint 는 후속 단계).

엔드포인트 (1단계 노출):
  GET /sections.json
  GET /accounts.json?section_id=
  GET /entries.json?section_id=&start_date=&end_date=

분당 20회 client-side throttle + 429 backoff 재시도. whooing-mcp-server-
wrapper 의 client.py 와 같은 규칙으로 동작한다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from whooing_tui.auth import WhooingAuth
from whooing_tui.errors import map_response, sanitize_token
from whooing_tui.models import ToolError

log = logging.getLogger(__name__)


def _coerce_dict(results: Any) -> dict[str, Any]:
    """mutation 응답이 dict / list[dict] / 그 외 어떤 형태로 와도 dict 1개로 정규화.

    후잉 응답 spec 이 mutating endpoint 에 대해 명시되지 않았으므로 보수적
    으로 처리: list 면 첫 element, 그 외는 빈 dict + raw 보존.
    """
    if isinstance(results, dict):
        return results
    if isinstance(results, list) and results and isinstance(results[0], dict):
        return results[0]
    return {"_raw": results} if results is not None else {}


DEFAULT_BASE = "https://whooing.com/api"

# 후잉 공식 한도: 분당 20 / 일 20,000. client-side 보수 throttle.
DEFAULT_RPM_CAP = 20
DEFAULT_RETRY_BACKOFF = (1.0, 2.0, 4.0, 8.0)  # 429 응답 시 max 4회


class WhooingClient:
    """thin httpx wrapper. 호출자(TUI/CLI) 입장에서는 dict/list 결과만 받는다."""

    def __init__(
        self,
        auth: WhooingAuth,
        base_url: str = DEFAULT_BASE,
        timeout: float = 10.0,
        rpm_cap: int = DEFAULT_RPM_CAP,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.rpm_cap = rpm_cap
        # 단일 프로세스 내의 요청 시각 sliding window.
        self._minute_window: list[float] = []
        self._lock = asyncio.Lock()

    # ---- rate limit ----------------------------------------------------

    async def _throttle(self) -> None:
        """분당 rpm_cap 초과 시 짧게 sleep. asyncio safe."""
        async with self._lock:
            now = time.monotonic()
            self._minute_window = [t for t in self._minute_window if now - t < 60]
            if len(self._minute_window) >= self.rpm_cap:
                oldest = self._minute_window[0]
                wait = 60.0 - (now - oldest) + 0.05
                log.debug(
                    "rate-limit throttle: %d req in last 60s, sleep %.2fs",
                    len(self._minute_window),
                    wait,
                )
                if wait > 0:
                    await asyncio.sleep(wait)
                now = time.monotonic()
                self._minute_window = [
                    t for t in self._minute_window if now - t < 60
                ]
            self._minute_window.append(now)

    # ---- HTTP ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """공통 HTTP 호출 — throttle + 429 backoff 재시도 + 응답 매핑.

        method: GET / POST / PUT / DELETE 중 하나. 본문 (json_body) 은
        POST/PUT 에서만 의미가 있으나 다른 메서드에 줘도 httpx 가 알아서
        무시한다 (RESTful 호환).

        후잉 응답이 비-JSON 이거나 응답 본문 code 가 4xx/5xx 인 경우
        ToolError 로 변환되어 raise (자세한 매핑은 errors.map_response).
        """
        url = f"{self.base_url}{path}"
        log.debug(
            "%s %s params=%s body=%s auth=%s",
            method.upper(), url, params,
            "<set>" if json_body is not None else None,
            sanitize_token(self.auth.token),
        )

        last_error: ToolError | None = None
        for attempt, backoff in enumerate(DEFAULT_RETRY_BACKOFF):
            await self._throttle()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.request(
                    method, url,
                    headers=self.auth.headers(),
                    params=params,
                    json=json_body,
                )
            try:
                return self._handle(r)
            except ToolError as e:
                if e.kind != "RATE_LIMIT":
                    raise
                # 429 → backoff 후 재시도. (402 일일은 retry 가치 없으므로 raise.)
                rest = e.details.get("rest_of_api")
                if rest is not None:
                    raise  # 일일 한도 — 재시도 안 함
                last_error = e
                log.warning(
                    "rate-limit 429 (attempt=%d), backoff %.1fs",
                    attempt + 1,
                    backoff,
                )
                await asyncio.sleep(backoff)

        # 모든 재시도 실패
        raise last_error or ToolError("RATE_LIMIT", "재시도 후에도 429")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json_body: dict[str, Any]) -> Any:
        return await self._request("POST", path, json_body=json_body)

    async def _put(self, path: str, json_body: dict[str, Any]) -> Any:
        return await self._request("PUT", path, json_body=json_body)

    async def _delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, params=params)

    def _handle(self, r: httpx.Response) -> Any:
        """공식 응답 포맷을 파싱해 results 만 추출 + 에러 매핑."""
        try:
            body = r.json()
        except Exception:
            raise ToolError(
                "UPSTREAM",
                f"비-JSON 응답 (status={r.status_code})",
                status=r.status_code,
                snippet=r.text[:200],
            )

        rest = body.get("rest_of_api")
        if rest is not None:
            log.debug("rest_of_api=%s", rest)

        # 후잉은 본문 code 와 HTTP status 가 다를 수 있어 본문 우선
        code = body.get("code", r.status_code)
        msg = body.get("message", "") or ""
        results = body.get("results")

        if code == 200:
            return results if results is not None else body
        if code == 204:
            return [] if results is None else results

        # 그 외는 errors 모듈에 위임
        raise map_response(code, msg, body, status=r.status_code)

    # ---- public API ---------------------------------------------------

    async def list_sections(self) -> list[dict[str, Any]]:
        """섹션(가계부) 목록. 응답은 list[dict] 로 정규화 (키 다양성 흡수)."""
        results = await self._get("/sections.json")
        return self._normalize_collection(results, key="sections")

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        """섹션의 계정 목록.

        응답 shape: {assets: [...], liabilities: [...], capital: [...],
        expenses: [...], income: [...]}. 본 메서드는 dict 그대로 반환 —
        type 별로 grouping 되어 있다. flatten 헬퍼가 [(id, name, type)]
        리스트로 변환.
        """
        results = await self._get("/accounts.json", params={"section_id": section_id})
        if isinstance(results, dict):
            return results
        return {}

    @staticmethod
    def flatten_accounts(accounts_dict: dict[str, Any]) -> list[dict[str, str]]:
        """{assets: [...]} → [{account_id, title, type}, ...]."""
        out: list[dict[str, str]] = []
        for type_key, items in accounts_dict.items():
            if not isinstance(items, list):
                continue
            for a in items:
                aid = a.get("account_id") or a.get("id")
                if not aid:
                    continue
                out.append({
                    "account_id": str(aid),
                    "title": a.get("title") or a.get("name") or "",
                    "type": type_key,
                })
        return out

    async def list_entries(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """후잉 entries fetch — 자동 분할 (1년 초과 + 100-cap pagination).

        후잉 API 응답 shape:
          results = {reports: [...], rows: [<entry>, ...]}
        server-side hard cap = 100 rows per request (`limit` param 무시).
        → 100 받으면 날짜 범위가 더 큰 가능성 → bisection 으로 분할.
        """
        from whooing_tui.dates import split_yearly_ranges

        ranges = split_yearly_ranges(start_date, end_date)
        out: list[dict[str, Any]] = []
        seen_ids: set = set()
        for s, e in ranges:
            chunks = await self._list_entries_chunked(section_id, s, e)
            for entry in chunks:
                eid = entry.get("entry_id")
                if eid and eid in seen_ids:
                    continue  # dedup across chunks
                if eid:
                    seen_ids.add(eid)
                out.append(entry)
        return out

    async def _list_entries_chunked(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Single date-range fetch with bisection if 100-cap hit."""
        from datetime import datetime, timedelta

        results = await self._get(
            "/entries.json",
            params={
                "section_id": section_id,
                "start_date": start_date,
                "end_date": end_date,
                "limit": 100,
            },
        )
        chunk = self._normalize_collection(results, key="rows")

        # 100 미만 → 모두 가져옴
        if len(chunk) < 100:
            return chunk

        # 100 hit — date range bisect
        if start_date == end_date:
            log.warning(
                "list_entries: %s 단일 일자에 100건 초과 (cap 도달) — "
                "100건만 반환됨 (서버가 추가 pagination 미지원). 누락 가능.",
                start_date,
            )
            return chunk

        # 중간 지점 계산
        s_dt = datetime.strptime(start_date, "%Y%m%d")
        e_dt = datetime.strptime(end_date, "%Y%m%d")
        mid_dt = s_dt + (e_dt - s_dt) // 2
        mid = mid_dt.strftime("%Y%m%d")
        next_dt = mid_dt + timedelta(days=1)
        next_str = next_dt.strftime("%Y%m%d")

        log.debug("list_entries: bisect %s~%s → [%s~%s, %s~%s]",
                  start_date, end_date, start_date, mid, next_str, end_date)

        left = await self._list_entries_chunked(section_id, start_date, mid)
        right = await self._list_entries_chunked(section_id, next_str, end_date)
        return left + right

    # ---- mutating endpoints ----------------------------------------------
    #
    # 후잉 REST 의 mutating endpoint 정확한 path 는 공식 docs 가 JS 로 렌더링
    # 되어 직접 추출이 어려웠다. 후잉 공식 MCP (`mcp__whooing__entries-*`)
    # 의 입력 schema 가 노출하는 필드 (section_id / l_account[_id] /
    # r_account[_id] / money / item / memo / entry_date) 를 그대로 body 로
    # 보내고, RESTful 가정 (POST /entries.json 으로 생성, PUT /entries/<id>
    # .json 으로 수정, DELETE /entries/<id>.json 으로 삭제) 로 시작한다.
    # 라이브 검증에서 실패하면 _entries_path / _entry_path 만 조정하면 된다.

    _ENTRIES_PATH = "/entries.json"

    @staticmethod
    def _entry_path(entry_id: str) -> str:
        return f"/entries/{entry_id}.json"

    async def create_entry(
        self,
        *,
        section_id: str,
        l_account: str,
        l_account_id: str,
        r_account: str,
        r_account_id: str,
        money: int,
        item: str = "",
        memo: str = "",
        entry_date: str | None = None,
    ) -> dict[str, Any]:
        """새 거래 입력. 성공 시 후잉이 반환한 results dict (entry_id 포함).

        money 는 음수도 허용하지 않는다 (후잉은 차변/대변으로 음양을 표현).
        호출자가 0/음수 검증을 책임진다.
        """
        body: dict[str, Any] = {
            "section_id": section_id,
            "l_account": l_account,
            "l_account_id": l_account_id,
            "r_account": r_account,
            "r_account_id": r_account_id,
            "money": int(money),
        }
        if item:
            body["item"] = item
        if memo:
            body["memo"] = memo
        if entry_date:
            body["entry_date"] = entry_date
        results = await self._post(self._ENTRIES_PATH, json_body=body)
        return _coerce_dict(results)

    async def update_entry(
        self,
        *,
        section_id: str,
        entry_id: str,
        l_account: str | None = None,
        l_account_id: str | None = None,
        r_account: str | None = None,
        r_account_id: str | None = None,
        money: int | None = None,
        item: str | None = None,
        memo: str | None = None,
        entry_date: str | None = None,
    ) -> dict[str, Any]:
        """기존 거래 수정 — 변경 필드만 보낸다.

        section_id / entry_id 외에 적어도 한 필드는 채워져 있어야 의미가
        있지만, 호출자가 그 검증을 책임진다 (전부 None 이어도 본 메서드는
        body 만 비워서 보낸다).
        """
        body: dict[str, Any] = {"section_id": section_id}
        for k, v in [
            ("l_account", l_account),
            ("l_account_id", l_account_id),
            ("r_account", r_account),
            ("r_account_id", r_account_id),
            ("item", item),
            ("memo", memo),
            ("entry_date", entry_date),
        ]:
            if v is not None:
                body[k] = v
        if money is not None:
            body["money"] = int(money)
        results = await self._put(self._entry_path(entry_id), json_body=body)
        return _coerce_dict(results)

    async def delete_entry(self, *, section_id: str, entry_id: str) -> dict[str, Any]:
        """거래 영구 삭제. 후잉은 soft-delete 가 아니므로 복구 불가."""
        results = await self._delete(
            self._entry_path(entry_id),
            params={"section_id": section_id},
        )
        return _coerce_dict(results)

    @staticmethod
    def _normalize_collection(results: Any, key: str) -> list[dict[str, Any]]:
        """후잉 응답이 list / {key: [...]} / {id: obj} 셋 다 가능하다.

        - list → 그대로
        - {key: [...]} → 해당 리스트
        - {id: obj} → values
        - 그 외 → []
        """
        if results is None:
            return []
        if isinstance(results, list):
            return results
        if isinstance(results, dict):
            if key in results and isinstance(results[key], list):
                return results[key]
            values = list(results.values())
            if values and all(isinstance(v, dict) for v in values):
                return values
        return []
