"""WhooingClient.list_frequent / create_frequent / delete_frequent — respx
mocks (0.84.0, 로드맵 P1-B).

후잉 REST:
  GET    /frequent_items.json                       → {slotN: [...]}
  POST   /frequent_items/<slot>.json
  DELETE /frequent_items/<slot>/<item_id>/<section_id>.json
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
async def test_list_frequent_flattens_slots_with_slot_key():
    respx.get("https://whooing.com/api/frequent_items.json").mock(
        return_value=Response(200, json={"code": 200, "results": {
            "slot1": [
                {"item_id": "f4", "item": "생필품", "money": 40000,
                 "l_account": "expenses", "l_account_id": "x12",
                 "r_account": "assets", "r_account_id": "x5"},
            ],
            "slot2": [
                {"item_id": "f9", "item": "커피", "money": 4500,
                 "l_account": "expenses", "l_account_id": "x20",
                 "r_account": "assets", "r_account_id": "x5"},
            ],
        }})
    )
    out = await _client().list_frequent(section_id="s1")
    assert len(out) == 2
    by_id = {r["item_id"]: r for r in out}
    assert by_id["f4"]["slot"] == "slot1"     # slot 키 부착.
    assert by_id["f9"]["slot"] == "slot2"
    assert by_id["f9"]["item"] == "커피"


@respx.mock
async def test_create_frequent_posts_to_slot_path():
    route = respx.post("https://whooing.com/api/frequent_items/slot1.json").mock(
        return_value=Response(200, json={"code": 200,
                                         "results": {"item_id": "f10"}})
    )
    out = await _client().create_frequent(
        section_id="s1", slot="slot1", item="커피",
        l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x5", money=4500,
    )
    assert out.get("item_id") == "f10"
    body = json.loads(route.calls[0].request.read())
    assert body["item"] == "커피"
    assert body["l_account"] == "expenses"
    assert body["money"] == 4500


@respx.mock
async def test_create_frequent_omits_money_when_none():
    route = respx.post("https://whooing.com/api/frequent_items/slot1.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}})
    )
    await _client().create_frequent(
        section_id="s1", item="교통",
        l_account="expenses", r_account="assets",
    )
    body = json.loads(route.calls[0].request.read())
    assert "money" not in body              # money None → 미전달.
    assert body["item"] == "교통"


@respx.mock
async def test_delete_frequent_path_includes_slot_item_section():
    route = respx.delete(
        "https://whooing.com/api/frequent_items/slot1/f4/s1.json"
    ).mock(return_value=Response(200, json={"code": 200}))
    await _client().delete_frequent(section_id="s1", slot="slot1", item_id="f4")
    assert route.called
