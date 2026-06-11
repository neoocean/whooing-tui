"""WhooingClient.create_account / update_account / delete_account /
check_account_deletable — respx mocks.

후잉 공식 MCP 의 `accounts-*` schema (확인 2026-05-10) 가 노출하는 입력
필드를 RESTful 가정 (POST /accounts.json, PUT /accounts/<id>.json,
DELETE /accounts/<id>.json, GET /accounts/<id>/check_deletable.json) 으로
호출하는 것을 검증. live 검증에서 path 가 다르면 client.py 의
`_ACCOUNTS_PATH` / `_account_path()` / `_account_check_deletable_path()`
만 조정하면 된다.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from whooing_tui.auth import WhooingAuth
from whooing_tui.cache import CacheStore
from whooing_tui.client import CachedWhooingClient, WhooingClient
from whooing_tui.models import ToolError


def _client() -> WhooingClient:
    return WhooingClient(
        auth=WhooingAuth(token="__eyJhfaketokenfortests1234"),
        base_url="https://whooing.com/api",
    )


# ---- create_account ----------------------------------------------------


@respx.mock
async def test_create_account_posts_required_fields():
    route = respx.post("https://whooing.com/api/accounts.json").mock(
        return_value=Response(
            200,
            json={"code": 200, "results": {"account_id": "x99"}},
        )
    )
    c = _client()
    out = await c.create_account(
        section_id="s1",
        account="expenses", type="account",
        title="식비", open_date="20240101",
    )
    assert out.get("account_id") == "x99"
    body = json.loads(route.calls[0].request.read())
    assert body == {
        "section_id": "s1",
        "account": "expenses",
        "type": "account",
        "title": "식비",
        "open_date": "20240101",
    }


@respx.mock
async def test_create_account_includes_optional_fields():
    route = respx.post("https://whooing.com/api/accounts.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"account_id": "x99"}})
    )
    c = _client()
    await c.create_account(
        section_id="s1",
        account="assets", type="account",
        title="신한카드", open_date="20240101",
        close_date="29991231",
        category="creditcard",
        memo="신한 메인 카드",
    )
    body = json.loads(route.calls[0].request.read())
    assert body["close_date"] == "29991231"
    assert body["category"] == "creditcard"
    assert body["memo"] == "신한 메인 카드"


@respx.mock
async def test_create_account_omits_unspecified_optional():
    route = respx.post("https://whooing.com/api/accounts.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"account_id": "x99"}})
    )
    c = _client()
    await c.create_account(
        section_id="s1",
        account="expenses", type="account",
        title="식비", open_date="20240101",
    )
    body = json.loads(route.calls[0].request.read())
    for k in ("close_date", "category", "memo"):
        assert k not in body, f"{k} 가 의도치 않게 포함됨"


# ---- update_account ----------------------------------------------------


@respx.mock
async def test_update_account_puts_full_required_set():
    """후잉 update 는 전체 필드 전달 정책."""
    route = respx.put("https://whooing.com/api/accounts/x20.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"account_id": "x20"}})
    )
    c = _client()
    await c.update_account(
        section_id="s1",
        account_id="x20",
        account="expenses", type="account",
        title="식비_갱신",
        open_date="20240101",
        close_date="29991231",
    )
    body = json.loads(route.calls[0].request.read())
    assert body == {
        "section_id": "s1",
        "account": "expenses",
        "type": "account",
        "title": "식비_갱신",
        "open_date": "20240101",
        "close_date": "29991231",
    }


@respx.mock
async def test_update_account_with_optional_category_memo():
    route = respx.put("https://whooing.com/api/accounts/x20.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _client()
    await c.update_account(
        section_id="s1", account_id="x20",
        account="expenses", type="account",
        title="식비", open_date="20240101", close_date="29991231",
        category="floating", memo="외식 / 배달",
    )
    body = json.loads(route.calls[0].request.read())
    assert body["category"] == "floating"
    assert body["memo"] == "외식 / 배달"


# ---- delete_account ---------------------------------------------------


@respx.mock
async def test_delete_account_uses_query_params():
    route = respx.delete("https://whooing.com/api/accounts/x20.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _client()
    await c.delete_account(section_id="s1", account="expenses", account_id="x20")
    req = route.calls[0].request
    assert req.method == "DELETE"
    assert b"section_id=s1" in req.url.query
    assert b"account=expenses" in req.url.query


# ---- check_deletable --------------------------------------------------


@respx.mock
async def test_check_account_deletable_uses_get_with_query():
    route = respx.get(
        "https://whooing.com/api/accounts/x20/check_deletable.json"
    ).mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": {"entries_count": 12, "balance": 100000, "is_last": False},
            },
        )
    )
    c = _client()
    out = await c.check_account_deletable(
        section_id="s1", account="expenses", account_id="x20",
    )
    assert out.get("entries_count") == 12
    assert out.get("balance") == 100000
    assert out.get("is_last") is False
    req = route.calls[0].request
    assert req.method == "GET"
    assert b"section_id=s1" in req.url.query
    assert b"account=expenses" in req.url.query


# ---- error 매핑 -------------------------------------------------------


@respx.mock
async def test_create_account_400_carries_error_parameters():
    respx.post("https://whooing.com/api/accounts.json").mock(
        return_value=Response(
            200,
            json={
                "code": 400, "message": "잘못된 파라미터",
                "error_parameters": {"open_date": "required"},
            },
        )
    )
    c = _client()
    with pytest.raises(ToolError) as ei:
        await c.create_account(
            section_id="s1", account="expenses", type="account",
            title="식비", open_date="invalid",
        )
    assert ei.value.kind == "USER_INPUT"
    assert ei.value.details.get("error_parameters") == {"open_date": "required"}


# ---- CachedWhooingClient 통합 -----------------------------------------


@respx.mock
async def test_cached_create_account_invalidates_accounts_and_entries():
    """create_account 후 accounts + entries 캐시 둘 다 비워져야."""
    respx.get("https://whooing.com/api/accounts.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"expenses": []}},
        )
    )
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"reports": [], "rows": []}},
        )
    )
    respx.post("https://whooing.com/api/accounts.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"account_id": "x99"}})
    )
    inner = _client()
    store = CacheStore(":memory:")
    cc = CachedWhooingClient(inner, store)
    # 캐시 채움
    await cc.list_accounts("s1")
    await cc.list_entries("s1", "20260501", "20260510")
    assert store.stats() == {"account_rows": 1, "entries_rows": 1}
    # mutation
    await cc.create_account(
        section_id="s1", account="expenses", type="account",
        title="식비", open_date="20240101",
    )
    assert store.stats() == {"account_rows": 0, "entries_rows": 0}


@respx.mock
async def test_cached_check_deletable_does_not_touch_cache():
    """check_deletable 은 단순 조회라 캐시 invalidate 안 함."""
    respx.get(
        "https://whooing.com/api/accounts/x20/check_deletable.json"
    ).mock(
        return_value=Response(200, json={"code": 200, "results": {"entries_count": 0}})
    )
    respx.get("https://whooing.com/api/accounts.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"expenses": []}})
    )
    inner = _client()
    store = CacheStore(":memory:")
    cc = CachedWhooingClient(inner, store)
    await cc.list_accounts("s1")
    assert store.stats()["account_rows"] == 1
    out = await cc.check_account_deletable(
        section_id="s1", account="expenses", account_id="x20",
    )
    assert out.get("entries_count") == 0
    # 캐시 그대로
    assert store.stats()["account_rows"] == 1


# ---- 감사 2026-06 §1-D: __getattr__ 폴백 ------------------------------


def test_cached_client_delegates_unknown_method_to_inner():
    """명시 정의 안 된 (public) 메서드는 inner 로 자동 위임 — 신규 endpoint
    가 CachedWhooingClient 에 pass-through 를 안 적어도 작동."""
    from whooing_tui.cache import CacheStore
    from whooing_tui.client import CachedWhooingClient, WhooingClient
    from whooing_tui.auth import WhooingAuth

    inner = WhooingClient(
        auth=WhooingAuth(token="__eyJhfaketokenfortests1234"),
        base_url="https://whooing.com/api",
    )
    # inner 에 가짜 신규 메서드 부착 (명시 wrapper 없음).
    async def _brand_new(self, **kw):
        return {"ok": kw}
    import types
    inner.brand_new = types.MethodType(_brand_new, inner)  # type: ignore[attr-defined]
    cc = CachedWhooingClient(inner, CacheStore(":memory:"))
    assert hasattr(cc, "brand_new")          # __getattr__ 폴백.
    assert cc.brand_new is inner.brand_new   # 같은 bound method.


def test_cached_client_private_attr_still_raises():
    from whooing_tui.cache import CacheStore
    from whooing_tui.client import CachedWhooingClient, WhooingClient
    from whooing_tui.auth import WhooingAuth
    cc = CachedWhooingClient(
        WhooingClient(auth=WhooingAuth(token="__eyJhfaketokenfortests1234"),
                      base_url="https://whooing.com/api"),
        CacheStore(":memory:"),
    )
    import pytest
    with pytest.raises(AttributeError):
        _ = cc._nonexistent_private
