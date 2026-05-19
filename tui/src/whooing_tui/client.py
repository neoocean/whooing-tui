"""후잉 REST API 클라이언트 — TUI 가 직접 사용.

읽기/쓰기 모두 후잉 공식 REST API 를 호출한다. 공식 MCP 서버를 거치지
않으므로 GET 외 POST/PUT/DELETE 도 지원한다 (1단계는 GET 위주, 거래
입력 등 mutating endpoint 는 후속 단계).

엔드포인트 (1단계 노출):
  GET /sections.json
  GET /accounts.json?section_id=
  GET /entries.json?section_id=&start_date=&end_date=

분당 20회 client-side throttle + 429 backoff 재시도. 본래 whooing-mcp-
server-wrapper (archived 2026-05-10) 의 client.py 와 같은 규칙으로 동작
하도록 만든 의도적 코드 중복 — wrapper 종료 후에도 그대로 유지.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

import httpx

# CL #53010+: list_entries 진행 콜백 — 사용자 UI 가 fetch 단계별 안내.
# kind: "fetch" | "received" | "bisect" | "yearly" | "done".
# 추가 정보 (count, mid, range_idx, total 등) 는 **extra 로 전달.
ProgressCallback = Callable[..., None]

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


def _drop_none(params: dict[str, Any]) -> dict[str, Any]:
    """None 값을 가진 키를 제거 — query string 에 빈 파라미터를 넣지 않게.
    CL #51117+ 보고서 endpoint 들에서 optional 파라미터 처리에 사용.
    """
    return {k: v for k, v in params.items() if v is not None}


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
        form_data: dict[str, Any] | None = None,
    ) -> Any:
        """공통 HTTP 호출 — throttle + 429 backoff 재시도 + 응답 매핑.

        method: GET / POST / PUT / DELETE 중 하나.
        - `json_body`: JSON 인코딩 body (Content-Type: application/json).
        - `form_data` (CL #52918+): form-urlencoded body (Content-Type:
          application/x-www-form-urlencoded). 후잉 API 의 POST/PUT 이
          JSON body 의 `section_id` 를 안 읽어 form-encoded 가 필요.

        한 호출에 둘 다 주지 말 것 — httpx 가 둘 중 하나를 우선.

        후잉 응답이 비-JSON 이거나 응답 본문 code 가 4xx/5xx 인 경우
        ToolError 로 변환되어 raise (자세한 매핑은 errors.map_response).
        """
        url = f"{self.base_url}{path}"
        log.debug(
            "%s %s params=%s json=%s form=%s auth=%s",
            method.upper(), url, params,
            "<set>" if json_body is not None else None,
            "<set>" if form_data is not None else None,
            sanitize_token(self.auth.token),
        )

        last_error: ToolError | None = None
        for attempt, backoff in enumerate(DEFAULT_RETRY_BACKOFF):
            await self._throttle()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                kwargs: dict[str, Any] = {
                    "headers": self.auth.headers(),
                    "params": params,
                }
                if form_data is not None:
                    kwargs["data"] = form_data
                elif json_body is not None:
                    kwargs["json"] = json_body
                r = await client.request(method, url, **kwargs)
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

    async def _delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        form_data: dict[str, Any] | None = None,
    ) -> Any:
        """DELETE 헬퍼 — query params + (CL #52979+) form-urlencoded body.

        후잉 server 는 POST/PUT 에서 JSON body 를 안 읽고 form-encoded 만
        인식 (CL #52918, 52928 의 누적 발견). DELETE 도 같은 정책으로
        section_id 를 body 에서 *읽으려* 시도 — query 만 보내면
        "`section_id` parameter is required." 로 거절 (사용자 보고
        2026-05-19, 중복 일괄 삭제 시).

        호출자는 query param 만 보내고 싶을 때 form_data=None, 양쪽
        send 가 필요하면 form_data=body 전달. `_request` 가 그대로 위임.
        """
        return await self._request(
            "DELETE", path, params=params, form_data=form_data,
        )

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
        *,
        on_progress: "ProgressCallback | None" = None,
    ) -> list[dict[str, Any]]:
        """후잉 entries fetch — 자동 분할 (1년 초과 + 100-cap pagination).

        후잉 API 응답 shape:
          results = {reports: [...], rows: [<entry>, ...]}
        server-side hard cap = 100 rows per request (`limit` param 무시).
        → 100 받으면 날짜 범위가 더 큰 가능성 → bisection 으로 분할.

        CL #53010+: `on_progress(kind, start, end, **extra)` 콜백 추가 —
        호출자가 사용자에게 진행 안내 (예: ScanProgressModal). kind 값:
          - "fetch"     — HTTP 요청 직전 (start/end).
          - "received"  — HTTP 응답 직후 (count=len(chunk)).
          - "bisect"    — 100건 한도 도달 — 분할 재요청 (mid=mid_date).
          - "yearly"    — 1년 분할 시 (range_idx, range_total).
          - "done"      — 전체 종료 (total=총 거래 수).
        sync 콜백 — 예외 raise 면 fetch 중단 (호출자 책임). None 이면 noop.
        """
        from whooing_tui.dates import split_yearly_ranges

        ranges = split_yearly_ranges(start_date, end_date)
        out: list[dict[str, Any]] = []
        seen_ids: set = set()
        for i, (s, e) in enumerate(ranges, start=1):
            if on_progress is not None:
                try:
                    on_progress(
                        "yearly", s, e,
                        range_idx=i, range_total=len(ranges),
                    )
                except Exception:  # pragma: no cover
                    log.exception("on_progress yearly callback raised")
            chunks = await self._list_entries_chunked(
                section_id, s, e, on_progress=on_progress,
            )
            for entry in chunks:
                eid = entry.get("entry_id")
                if eid and eid in seen_ids:
                    continue  # dedup across chunks
                if eid:
                    seen_ids.add(eid)
                out.append(entry)
        if on_progress is not None:
            try:
                on_progress(
                    "done", start_date, end_date, total=len(out),
                )
            except Exception:  # pragma: no cover
                log.exception("on_progress done callback raised")
        return out

    async def _list_entries_chunked(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
        *,
        on_progress: "ProgressCallback | None" = None,
    ) -> list[dict[str, Any]]:
        """Single date-range fetch with bisection if 100-cap hit."""
        from datetime import datetime, timedelta

        if on_progress is not None:
            try:
                on_progress("fetch", start_date, end_date)
            except Exception:  # pragma: no cover
                log.exception("on_progress fetch callback raised")
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
        if on_progress is not None:
            try:
                on_progress(
                    "received", start_date, end_date, count=len(chunk),
                )
            except Exception:  # pragma: no cover
                log.exception("on_progress received callback raised")

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
        if on_progress is not None:
            try:
                on_progress(
                    "bisect", start_date, end_date,
                    mid=mid, next_start=next_str,
                )
            except Exception:  # pragma: no cover
                log.exception("on_progress bisect callback raised")

        left = await self._list_entries_chunked(
            section_id, start_date, mid, on_progress=on_progress,
        )
        right = await self._list_entries_chunked(
            section_id, next_str, end_date, on_progress=on_progress,
        )
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

        CL #52911+ (사용자 보고): 카드 명세서 일괄 import 16/16 모두
        "`section_id` parameter is required." 로 실패. 후잉 server 가 POST
        의 JSON body 의 section_id 를 인식하지 못함. *query string* 으로도
        보내야 받아준다 (`/sections/{id}/entries.json` URL syntax 대안).
        body 에도 그대로 두어 향후 API 변경에 안전 — 양쪽 send 정책.
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
        # CL #52918+: 후잉 API 가 POST 의 JSON body 를 안 읽고
        # form-urlencoded 만 인식 → CL #52911 의 query-param 만으로는
        # 부족 (사용자 보고: 16/16 모두 같은 에러 재발). form-encoded 로
        # 전체 body 를 보내고 section_id 는 query 로도 belt-and-suspenders.
        results = await self._request(
            "POST", self._ENTRIES_PATH,
            params={"section_id": section_id},
            form_data=body,
        )
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
        # CL #52918+: create_entry 와 동일 — form-urlencoded + query 양쪽.
        results = await self._request(
            "PUT", self._entry_path(entry_id),
            params={"section_id": section_id},
            form_data=body,
        )
        return _coerce_dict(results)

    async def delete_entry(self, *, section_id: str, entry_id: str) -> dict[str, Any]:
        """거래 영구 삭제. 후잉은 soft-delete 가 아니므로 복구 불가.

        구현 정책 (CL #53015+ — 사용자 보고 2026-05-19 *2회 재발*):
        후잉 REST `DELETE /entries/{id}.json` 가 section_id 를 어떤 방식
        (query / form body / 양쪽) 으로 보내도 "`section_id` parameter is
        required." 로 거절. CL #52979 의 form-body 추가 시도도 실서버에서
        실패 (httpx 가 정확히 body 를 보내는 건 unit-test 로 검증됐으나
        server 가 DELETE body 를 안 읽는 듯).

        **→ 본 메서드는 공식 후잉 MCP server (`tools/call` →
        `entries-delete`) 로 위임.** 보고서 / 통계 endpoint (CL #52755+)
        와 동일 정책. MCP server 는 같은 동작을 안정적으로 처리한다.

        MCP 호출이 실패하면 (네트워크 일시 장애 / 토큰 만료 등) REST
        DELETE 를 fallback 으로 시도 — 정상 동작 환경 확보 + 단일 보호.
        """
        from whooing_tui.official_mcp import OfficialMcpError
        try:
            results = await self.call_official_tool(
                "entries-delete",
                {"section_id": section_id, "entry_id": entry_id},
            )
            return _coerce_dict(results)
        except OfficialMcpError as e:
            log.warning(
                "delete_entry via official MCP failed (%s) — REST fallback",
                e,
            )
            # Fallback — 적어도 한 번 더 시도. 본 서버가 form-body 를 안 읽으면
            # 여전히 같은 에러 — 사용자에게 MCP 오류 진단 정보로 활용.
            results = await self._delete(
                self._entry_path(entry_id),
                params={"section_id": section_id},
                form_data={"section_id": section_id},
            )
            return _coerce_dict(results)

    # ---- accounts CRUD ---------------------------------------------------
    #
    # 후잉 공식 MCP 의 `accounts-create/update/delete/check_deletable` schema
    # (확인됨 2026-05-10) 가 노출하는 입력 필드를 그대로 body 로 보내고,
    # RESTful 가정 (POST /accounts.json, PUT /accounts/<id>.json,
    # DELETE /accounts/<id>.json, GET /accounts/<id>/check_deletable.json)
    # 으로 호출. 라이브 검증에서 path 가 다르면 _ACCOUNTS_PATH /
    # _account_path() 만 조정하면 된다.

    _ACCOUNTS_PATH = "/accounts.json"

    @staticmethod
    def _account_path(account_id: str) -> str:
        return f"/accounts/{account_id}.json"

    @staticmethod
    def _account_check_deletable_path(account_id: str) -> str:
        return f"/accounts/{account_id}/check_deletable.json"

    async def create_account(
        self,
        *,
        section_id: str,
        account: str,                # assets/liabilities/capital/expenses/income
        type: str,                   # account / group
        title: str,
        open_date: str,              # YYYYMMDD — 거래 기록 시작 기준
        close_date: str | None = None,   # 미지정 = 무기한 (29991231)
        category: str | None = None,     # normal/client/creditcard/checkcard/steady/floating
        memo: str | None = None,
    ) -> dict[str, Any]:
        """새 계정과목 추가. open_date 는 거래 기록 시작 기준이라 호출자가 사용자 확인 필수."""
        body: dict[str, Any] = {
            "section_id": section_id,
            "account": account,
            "type": type,
            "title": title,
            "open_date": open_date,
        }
        for k, v in [
            ("close_date", close_date),
            ("category", category),
            ("memo", memo),
        ]:
            if v is not None:
                body[k] = v
        results = await self._post(self._ACCOUNTS_PATH, json_body=body)
        return _coerce_dict(results)

    async def update_account(
        self,
        *,
        section_id: str,
        account_id: str,
        account: str,
        type: str,
        title: str,
        open_date: str,
        close_date: str,             # 후잉 update 는 전체 필드 전달이 정책 (close 도 필수)
        category: str | None = None,
        memo: str | None = None,
    ) -> dict[str, Any]:
        """계정과목 수정 — 전체 필드 전달이 후잉 정책 (변경 안 한 필드도 동봉)."""
        body: dict[str, Any] = {
            "section_id": section_id,
            "account": account,
            "type": type,
            "title": title,
            "open_date": open_date,
            "close_date": close_date,
        }
        for k, v in [("category", category), ("memo", memo)]:
            if v is not None:
                body[k] = v
        results = await self._put(self._account_path(account_id), json_body=body)
        return _coerce_dict(results)

    async def delete_account(
        self,
        *,
        section_id: str,
        account: str,
        account_id: str,
    ) -> dict[str, Any]:
        """계정과목 강제 삭제. 거래 내역이 있으면 거부될 수 있음 — 호출 전
        `check_account_deletable` 로 확인하는 것을 권장.
        """
        # CL #52979+: DELETE 도 form-body 동봉 (delete_entry 와 동일 정책).
        results = await self._delete(
            self._account_path(account_id),
            params={"section_id": section_id, "account": account},
            form_data={"section_id": section_id, "account": account},
        )
        return _coerce_dict(results)

    async def check_account_deletable(
        self,
        *,
        section_id: str,
        account: str,
        account_id: str,
    ) -> dict[str, Any]:
        """삭제 전 사전 검사 — 거래 건수 / 잔액 / 마지막 항목 여부 등."""
        results = await self._get(
            self._account_check_deletable_path(account_id),
            params={"section_id": section_id, "account": account},
        )
        return _coerce_dict(results)

    # ---- report / budget / goal endpoints (CL #51117+) -------------------
    #
    # CL #51116 의 첫 시도는 `/reports.json` 단일 endpoint + `type` query
    # 로 dispatch 한다고 추측했는데, 라이브 호출 결과 모든 보고서가
    # `unknown method` 응답을 받았다 (사용자 보고). 실 API 는 endpoint 별
    # 별도 path 를 가진다 — `whooing://api-docs` 리소스에서 확인:
    #
    #   /report.json                          (account=all 또는 account_id 지정시 query)
    #   /report/<account>.json                (account 가 path 로 들어가는 변형)
    #   /report/<account>/<account_id>.json   (account_id 까지 path)
    #   /report_summary.json
    #   /report_summary/<account>.json
    #   /in_out.json (or /in_out/<account>[/<account_id>].json)
    #   /calendar.json
    #   /bill.json (or /bill/<account_id>.json)
    #   /checkcard.json (or /checkcard/<account_id>.json)
    #   /budget/<account>.json                (account = expenses / income, path 필수)
    #   /budget_goal.json
    #   /goal.json
    #   /main/report_customs.json?action=list|info[&customId=...]
    #   /entries/latest.json, /entries/latest_items.json,
    #   /entries/flow_of_account.json, /entries/flow_of_account_id.json,
    #   /entries/changes_of_account_id.json, /entries/changes_of_client.json,
    #   /entries/changes_of_item.json, /entries/account_ids_of_account.json,
    #   /entries/clients_of_account_id.json, /entries/items_of_account_id.json
    #
    # MCP 의 `cashflow` type 은 실 API 에 대응 endpoint 가 없어 본 클라이언트
    # 에서는 지원하지 않는다 (메뉴에서도 제거).

    async def get_report(
        self,
        *,
        section_id: str,
        account: str | None = None,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        rows_type: str | None = None,
        item: str | None = None,
    ) -> Any:
        """`/report[/<account>[/<account_id>]].json` — 통합 재무 보고서.

        `account` 는 콤마 구분 다중 가능 (예: `expenses,income`). `account_id`
        는 `account` 와 함께 path 로. 둘 다 None 이면 `/report.json` (root).
        """
        path = "/report.json"
        if account:
            if account_id:
                path = f"/report/{account}/{account_id}.json"
            else:
                path = f"/report/{account}.json"
        return await self._get(
            path, params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
                "rows_type": rows_type, "item": item,
            }),
        )

    async def get_report_summary(
        self,
        *,
        section_id: str,
        account: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        rows_type: str | None = None,
        item: str | None = None,
    ) -> Any:
        """`/report_summary[/<account>].json` — flat 숫자 응답."""
        path = (
            f"/report_summary/{account}.json" if account
            else "/report_summary.json"
        )
        return await self._get(
            path, params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
                "rows_type": rows_type, "item": item,
            }),
        )

    async def get_in_out(
        self,
        *,
        section_id: str,
        account: str | None = None,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """`/in_out[/<account>[/<account_id>]].json` — 항목별 증감 보고서."""
        path = "/in_out.json"
        if account:
            if account_id:
                path = f"/in_out/{account}/{account_id}.json"
            else:
                path = f"/in_out/{account}.json"
        return await self._get(
            path, params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
            }),
        )

    async def get_calendar(
        self,
        *,
        section_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """`/calendar.json` — 월별/일별 수익·비용·기타 거래 액수."""
        return await self._get(
            "/calendar.json", params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
            }),
        )

    async def get_bill(
        self,
        *,
        section_id: str,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """`/bill[/<account_id>].json` — 신용카드 청구내역."""
        path = (
            f"/bill/{account_id}.json" if account_id else "/bill.json"
        )
        return await self._get(
            path, params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
            }),
        )

    async def get_checkcard(
        self,
        *,
        section_id: str,
        account_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """`/checkcard[/<account_id>].json` — 체크카드 사용내역."""
        path = (
            f"/checkcard/{account_id}.json" if account_id
            else "/checkcard.json"
        )
        return await self._get(
            path, params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
            }),
        )

    async def get_budget(
        self,
        *,
        section_id: str,
        account: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """`/budget/<account>.json` — 예산 대비 실적. `account` 필수
        (expenses / income), path 로 들어간다."""
        return await self._get(
            f"/budget/{account}.json",
            params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
            }),
        )

    async def get_budget_goal(self, *, section_id: str) -> dict[str, Any]:
        """`/budget_goal.json` — 장기목표 설정."""
        results = await self._get(
            "/budget_goal.json", params={"section_id": section_id},
        )
        return _coerce_dict(results)

    async def get_goal(
        self,
        *,
        section_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Any:
        """`/goal.json` — 월별 자본 목표값 (장기목표 파생)."""
        return await self._get(
            "/goal.json", params=_drop_none({
                "section_id": section_id,
                "start_date": start_date, "end_date": end_date,
            }),
        )

    async def list_report_customs(
        self,
        *,
        section_id: str,
        report: str,
    ) -> list[dict[str, Any]]:
        """`/main/report_customs.json?action=list&report=<>` — 사용자 정의
        보고서 행 목록. `report` = report_bs / report_pl.

        실 API 응답은 `{status, rows: [...]}`. 본 메서드는 `_normalize_collection`
        으로 list 만 추출.
        """
        results = await self._get(
            "/main/report_customs.json",
            params={
                "section_id": section_id, "report": report, "action": "list",
            },
        )
        return self._normalize_collection(results, key="rows")

    async def get_report_custom(
        self,
        *,
        section_id: str,
        report: str,
        custom_id: str,
    ) -> dict[str, Any]:
        """`/main/report_customs.json?action=info&customId=<>&report=<>` — 단건."""
        results = await self._get(
            "/main/report_customs.json",
            params={
                "section_id": section_id, "report": report,
                "action": "info", "customId": custom_id,
            },
        )
        return _coerce_dict(results)

    async def get_entries_latest(
        self,
        *,
        section_id: str,
        max: str | None = None,
        limit: int | None = None,
    ) -> Any:
        """`/entries/latest.json` — 최근 거래내역."""
        return await self._get(
            "/entries/latest.json",
            params=_drop_none({
                "section_id": section_id, "max": max, "limit": limit,
            }),
        )

    # ---- 공식 후잉 MCP 위임 (CL #52755+) ---------------------------------
    #
    # 우리 REST path 추측이 일부 endpoint (`/report/{account}.json` 등) 에서
    # 403 으로 실패. 후잉이 공식 MCP server (`https://whooing.com/mcp`) 를
    # 운영하고 거기에는 정확한 도구 스키마가 노출돼 있다. 보고서 fetch 는
    # 그것을 위임하면 path 추측 자체가 사라짐.

    async def call_official_tool(
        self, name: str, arguments: dict[str, Any],
    ) -> Any:
        """공식 후잉 MCP server 의 도구 호출 (tools/call).

        본 메서드는 `OfficialMcpClient` 의 thin wrapper — 우리 토큰 사용,
        매 호출마다 새 client 인스턴스 (간단; 후잉 MCP server 가 stateless
        라 connection reuse 필요성 작음).

        예외:
          OfficialMcpError 가 raise — caller 가 잡아 ToolError 로 변환할 수
          있으나 본 wrapper 는 raw 노출 (호출자가 도구 별로 다른 처리).
        """
        from whooing_tui.official_mcp import OfficialMcpClient
        om = OfficialMcpClient(self.auth.token)
        return await om.call_tool(name, arguments)

    # ---- monthly entries (정기/반복) — CL #51152+ -----------------------
    # 후잉 공식 docs 의 정확한 path 미공개 — 추정 RESTful 패턴.
    # 라이브 검증 시 path 가 다르면 `_monthly_path` / `_monthly_collection_path`
    # 만 조정. 사용 가능성 높은 후보 (관찰):
    #   /monthly.json                — collection list / create
    #   /monthly/<id>.json           — single update / delete
    #   /entry_monthly.json          — alternative
    # 첫 번째 후보로 시도, 실패 시 ToolError("ENDPOINT_UNKNOWN", ...).

    @staticmethod
    def _monthly_collection_path() -> str:
        return "/monthly.json"

    @staticmethod
    def _monthly_path(monthly_id: str) -> str:
        return f"/monthly/{monthly_id}.json"

    async def list_monthly(self, *, section_id: str) -> list[dict[str, Any]]:
        """매월 입력 거래 (반복 거래) 의 list. CL #51152+ 추정 endpoint.

        spec 미확인 — 첫 호출 실패 시 ToolError 가 caller 에 전달돼 사용자
        라이브 검증 안내. 실 path 가 다르면 `_monthly_collection_path` 만
        조정해 즉시 정상 작동.
        """
        results = await self._get(
            self._monthly_collection_path(),
            params={"section_id": section_id},
        )
        return self._normalize_collection(results, key="rows")

    async def create_monthly(
        self,
        *,
        section_id: str,
        target_day: int,
        l_account: str,
        l_account_id: str,
        r_account: str,
        r_account_id: str,
        money: int,
        item: str = "",
        memo: str = "",
    ) -> dict[str, Any]:
        """매월 입력 거래 신규. `target_day` = 1~31 (예: 25일).

        다른 필드는 일반 entry 와 동일.
        """
        body: dict[str, Any] = {
            "section_id": section_id,
            "target_day": int(target_day),
            "l_account": l_account, "l_account_id": l_account_id,
            "r_account": r_account, "r_account_id": r_account_id,
            "money": int(money),
            "item": item, "memo": memo,
        }
        results = await self._post(self._monthly_collection_path(), json_body=body)
        return _coerce_dict(results)

    async def delete_monthly(
        self, *, section_id: str, monthly_id: str,
    ) -> dict[str, Any]:
        """매월 입력 거래 삭제. CL #51152+.

        CL #52979+: DELETE 도 form-body 동봉 (delete_entry 와 동일 정책).
        """
        results = await self._delete(
            self._monthly_path(monthly_id),
            params={"section_id": section_id},
            form_data={"section_id": section_id},
        )
        return _coerce_dict(results)

    # ---- budget setter — CL #51153+ --------------------------------------
    # 후잉의 budget 은 `/budget/<account>.json` (account = expenses / income)
    # GET 으로 조회 가능. setter 는 동일 path POST 또는 PUT 추정.

    async def set_budget(
        self,
        *,
        section_id: str,
        account: str,            # 'expenses' | 'income'
        account_id: str,         # 어느 항목 (예: x50 식비)
        amount: int,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """예산 1건 set/update. CL #51153+ 추정 endpoint.

        같은 (section_id, account_id, period) 면 upsert 가정. 라이브 검증
        실패 시 `_budget_path` 변경 또는 PUT 으로 전환.
        """
        body: dict[str, Any] = _drop_none({
            "section_id": section_id,
            "account_id": account_id,
            "amount": int(amount),
            "start_date": start_date, "end_date": end_date,
        })
        results = await self._post(
            f"/budget/{account}.json", json_body=body,
        )
        return _coerce_dict(results)

    async def delete_budget(
        self,
        *,
        section_id: str,
        account: str,
        account_id: str,
    ) -> dict[str, Any]:
        """예산 1건 제거. CL #51153+.

        CL #52979+: DELETE 도 form-body 동봉 (delete_entry 와 동일 정책).
        """
        body = {"section_id": section_id, "account_id": account_id}
        results = await self._delete(
            f"/budget/{account}.json",
            params=body,
            form_data=body,
        )
        return _coerce_dict(results)

    # ---- goal setter — CL #51154+ ----------------------------------------

    async def set_budget_goal(
        self,
        *,
        section_id: str,
        amount: int,
        target_date: str | None = None,
    ) -> dict[str, Any]:
        """장기목표 set. CL #51154+ 추정 endpoint."""
        body = _drop_none({
            "section_id": section_id,
            "amount": int(amount),
            "target_date": target_date,
        })
        results = await self._post("/budget_goal.json", json_body=body)
        return _coerce_dict(results)

    async def set_goal(
        self,
        *,
        section_id: str,
        target_month: str,    # YYYYMM
        amount: int,
    ) -> dict[str, Any]:
        """월별 자본 목표값 set. CL #51154+ 추정 endpoint.

        장기목표 파생이지만 month-별 override 가능 (후잉의 일반적 패턴).
        """
        body = {
            "section_id": section_id,
            "target_month": target_month,
            "amount": int(amount),
        }
        results = await self._post("/goal.json", json_body=body)
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


class CachedWhooingClient:
    """sqlite-backed 캐시를 두른 WhooingClient — 같은 인터페이스.

    화면 코드 (HomeScreen / EntriesScreen) 는 본 wrapper 와 raw client 를
    구분할 필요가 없도록 같은 메서드 시그니처를 제공. 캐시 정책:
      - sections   : 캐시 X (작고 자주 안 부르고, mutation 영향 없음)
      - accounts   : TTL 1시간 + 외부 invalidate
      - entries    : TTL 5분 + mutation 시 invalidate
      - mutations  : 그대로 inner 위임 + 해당 섹션 entries 캐시 invalidate

    `accounts_ttl_sec=-1` / `entries_ttl_sec=-1` 로 TTL 무시 (영구 캐시).
    """

    def __init__(
        self,
        inner: "WhooingClient",
        store: "CacheStore",
        *,
        accounts_ttl_sec: int = 3600,
        entries_ttl_sec: int = 300,
    ) -> None:
        self._inner = inner
        self._store = store
        self._accounts_ttl_sec = accounts_ttl_sec
        self._entries_ttl_sec = entries_ttl_sec

    # 공통 패스스루 — auth 등 raw 속성을 그대로 노출 (테스트 / 디버깅)
    @property
    def auth(self):  # type: ignore[no-untyped-def]
        return self._inner.auth

    # ---- read ---------------------------------------------------------

    async def list_sections(self) -> list[dict[str, Any]]:
        return await self._inner.list_sections()

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        cached = self._store.get_accounts(
            section_id, max_age_sec=self._accounts_ttl_sec,
        )
        if cached is not None:
            return cached
        result = await self._inner.list_accounts(section_id)
        if isinstance(result, dict) and result:
            self._store.put_accounts(section_id, result)
        return result

    async def list_entries(
        self, section_id: str, start_date: str, end_date: str,
        *,
        on_progress: "ProgressCallback | None" = None,
    ) -> list[dict[str, Any]]:
        cached = self._store.get_entries(
            section_id, start_date, end_date,
            max_age_sec=self._entries_ttl_sec,
        )
        if cached is not None:
            # 캐시 hit 도 사용자에게 알림 — 콜백이 있으면 done 한 번 발사.
            if on_progress is not None:
                try:
                    on_progress(
                        "cache_hit", start_date, end_date, total=len(cached),
                    )
                except Exception:  # pragma: no cover
                    log.exception("on_progress cache_hit raised")
            return cached
        result = await self._inner.list_entries(
            section_id, start_date, end_date, on_progress=on_progress,
        )
        # 100-cap 도달 의심되는 응답도 같은 라운드에선 캐시 — TTL 짧음.
        self._store.put_entries(section_id, start_date, end_date, result)
        return result

    # accounts flat 변환은 raw client 와 동일
    flatten_accounts = staticmethod(WhooingClient.flatten_accounts)

    # ---- mutate (캐시 invalidate) -------------------------------------

    async def create_entry(self, **kwargs) -> dict[str, Any]:
        out = await self._inner.create_entry(**kwargs)
        self._store.invalidate_entries(kwargs.get("section_id"))
        return out

    async def update_entry(self, **kwargs) -> dict[str, Any]:
        out = await self._inner.update_entry(**kwargs)
        self._store.invalidate_entries(kwargs.get("section_id"))
        return out

    async def delete_entry(self, **kwargs) -> dict[str, Any]:
        out = await self._inner.delete_entry(**kwargs)
        self._store.invalidate_entries(kwargs.get("section_id"))
        return out

    # accounts CRUD — accounts 캐시 + entries 캐시 둘 다 invalidate.
    # entries 응답이 account 정보 (title 등) 를 포함할 가능성 있어 안전을
    # 위해 같이 비운다.

    async def create_account(self, **kwargs) -> dict[str, Any]:
        out = await self._inner.create_account(**kwargs)
        self._store.invalidate_accounts(kwargs.get("section_id"))
        self._store.invalidate_entries(kwargs.get("section_id"))
        return out

    async def update_account(self, **kwargs) -> dict[str, Any]:
        out = await self._inner.update_account(**kwargs)
        self._store.invalidate_accounts(kwargs.get("section_id"))
        self._store.invalidate_entries(kwargs.get("section_id"))
        return out

    async def delete_account(self, **kwargs) -> dict[str, Any]:
        out = await self._inner.delete_account(**kwargs)
        self._store.invalidate_accounts(kwargs.get("section_id"))
        self._store.invalidate_entries(kwargs.get("section_id"))
        return out

    async def check_account_deletable(self, **kwargs) -> dict[str, Any]:
        # 단순 조회라 캐시 영향 없음 — 그대로 위임.
        return await self._inner.check_account_deletable(**kwargs)

    # 보고서 / 예산 / 목표 — CL #51116+ (path 수정 #51117). 모두 단순 조회.
    async def get_report(self, **kwargs) -> Any:
        return await self._inner.get_report(**kwargs)

    async def get_report_summary(self, **kwargs) -> Any:
        return await self._inner.get_report_summary(**kwargs)

    async def get_in_out(self, **kwargs) -> Any:
        return await self._inner.get_in_out(**kwargs)

    async def get_calendar(self, **kwargs) -> Any:
        return await self._inner.get_calendar(**kwargs)

    async def get_bill(self, **kwargs) -> Any:
        return await self._inner.get_bill(**kwargs)

    async def get_checkcard(self, **kwargs) -> Any:
        return await self._inner.get_checkcard(**kwargs)

    async def list_report_customs(self, **kwargs) -> list[dict[str, Any]]:
        return await self._inner.list_report_customs(**kwargs)

    async def get_report_custom(self, **kwargs) -> dict[str, Any]:
        return await self._inner.get_report_custom(**kwargs)

    async def get_budget(self, **kwargs) -> Any:
        return await self._inner.get_budget(**kwargs)

    async def get_budget_goal(self, **kwargs) -> dict[str, Any]:
        return await self._inner.get_budget_goal(**kwargs)

    async def get_goal(self, **kwargs) -> Any:
        return await self._inner.get_goal(**kwargs)

    async def get_entries_latest(self, **kwargs) -> Any:
        return await self._inner.get_entries_latest(**kwargs)

    # CL #52755+: 공식 후잉 MCP 위임 (보고서 fetch 가 사용). CachedWhooingClient
    # 도 같은 메서드 노출해야 reports.py 에서 직접 호출 가능 — 사용자 보고
    # `'CachedWhooingClient' object has no attribute 'call_official_tool'`
    # 회귀 fix (CL #52765).
    async def call_official_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._inner.call_official_tool(name, arguments)

    # CL #52896+: 매월입력 / 예산 / 목표 mutation 메서드 pass-through. 누락
    # 시 `'CachedWhooingClient' object has no attribute 'list_monthly'` 등
    # AttributeError — 사용자 보고 (MonthlyEntriesScreen). 같은 패턴의 회귀
    # (call_official_tool 누락 / CL #52765) 와 동일 원인 — Cached wrapper
    # 가 새 endpoint 마다 명시 노출 필요.
    async def list_monthly(self, **kwargs) -> list[dict[str, Any]]:
        return await self._inner.list_monthly(**kwargs)

    async def create_monthly(self, **kwargs) -> dict[str, Any]:
        return await self._inner.create_monthly(**kwargs)

    async def delete_monthly(self, **kwargs) -> dict[str, Any]:
        return await self._inner.delete_monthly(**kwargs)

    async def set_budget(self, **kwargs) -> Any:
        return await self._inner.set_budget(**kwargs)

    async def delete_budget(self, **kwargs) -> Any:
        return await self._inner.delete_budget(**kwargs)

    async def set_budget_goal(self, **kwargs) -> Any:
        return await self._inner.set_budget_goal(**kwargs)

    async def set_goal(self, **kwargs) -> Any:
        return await self._inner.set_goal(**kwargs)

    # 사용자가 'r' 누르면 호출 — 화면이 직접 강제 재로드 가능.
    def invalidate_section(self, section_id: str) -> None:
        self._store.invalidate_accounts(section_id)
        self._store.invalidate_entries(section_id)


# 순환 import 회피 — 본 모듈 끝에서 import.
from whooing_tui.cache import CacheStore  # noqa: E402
