"""WhooingClient.create_section / update_section / delete_section /
sort_sections / sort_accounts — respx mocks (0.84.0, 로드맵 P3-C).

후잉 REST 가정:
  POST   /sections.json
  PUT    /sections/<id>.json
  DELETE /sections/<id>.json
  PUT    /sections/sort.json          (section_ids 콤마 문자열)
  PUT    /accounts/<account>/sort.json (account_ids 콤마 문자열)
live 검증에서 path 가 다르면 client.py 해당 메서드의 path 만 조정.
"""

from __future__ import annotations

import json

import respx
from httpx import Response

from whooing_tui.auth import WhooingAuth
from whooing_tui.client import WhooingClient


def _client() -> WhooingClient:
    return WhooingClient(
        auth=WhooingAuth(token="__eyJhfaketokenfortests1234"),
        base_url="https://whooing.com/api",
    )


@respx.mock
async def test_create_section_posts_title_and_currency():
    route = respx.post("https://whooing.com/api/sections.json").mock(
        return_value=Response(200, json={"code": 200,
                                         "results": {"section_id": "s9"}})
    )
    c = _client()
    out = await c.create_section(title="가계부 2026")
    assert out.get("section_id") == "s9"
    body = json.loads(route.calls[0].request.read())
    assert body["title"] == "가계부 2026"
    assert body["currency"] == "KRW"      # 기본 통화.


@respx.mock
async def test_create_section_includes_memo_and_currency():
    route = respx.post("https://whooing.com/api/sections.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _client()
    await c.create_section(title="USD 가계부", currency="USD", memo="여행용")
    body = json.loads(route.calls[0].request.read())
    assert body["currency"] == "USD"
    assert body["memo"] == "여행용"


@respx.mock
async def test_update_section_puts_only_changed_fields():
    route = respx.put("https://whooing.com/api/sections/s9.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    c = _client()
    await c.update_section(section_id="s9", title="새 이름")
    body = json.loads(route.calls[0].request.read())
    assert body == {"title": "새 이름"}     # currency/memo 미전달.


@respx.mock
async def test_delete_section_hits_delete_endpoint():
    route = respx.delete("https://whooing.com/api/sections/s9.json").mock(
        return_value=Response(200, json={"code": 200})
    )
    c = _client()
    await c.delete_section(section_id="s9")
    assert route.called


@respx.mock
async def test_sort_sections_joins_ids_with_comma():
    route = respx.put("https://whooing.com/api/sections/sort.json").mock(
        return_value=Response(200, json={"code": 200, "results": []})
    )
    c = _client()
    await c.sort_sections(section_ids=["s3", "s1", "s2"])
    body = json.loads(route.calls[0].request.read())
    assert body["section_ids"] == "s3,s1,s2"


@respx.mock
async def test_sort_accounts_joins_ids_and_includes_section():
    route = respx.put(
        "https://whooing.com/api/accounts/expenses/sort.json"
    ).mock(return_value=Response(200, json={"code": 200, "results": []}))
    c = _client()
    await c.sort_accounts(
        section_id="s1", account="expenses",
        account_ids=["x20", "x21", "x22"],
    )
    body = json.loads(route.calls[0].request.read())
    assert body["section_id"] == "s1"
    assert body["account_ids"] == "x20,x21,x22"
