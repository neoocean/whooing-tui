"""외부입력(임시저장소) 클라이언트 + 순수 헬퍼 — respx mocks.

후잉 비공식 내부 엔드포인트(웹 UI `main_insert_outside.js` 에서 역설계)를
본 클라이언트가 정확한 형태로 호출하는지 검증. 라이브에서 형태가 바뀌면
`outside.py` 의 path/param 만 조정.
"""

from __future__ import annotations

from urllib.parse import parse_qs

import respx
from httpx import Response

from whooing_tui.auth import WhooingAuth
from whooing_tui.outside import (
    OutsideClient,
    OutsideError,
    build_entry,
    parse_counter_account,
    staged_item_text,
)

BASE = "https://whooing.com/api"


def _client() -> OutsideClient:
    return OutsideClient(
        WhooingAuth(token="__eyJhfaketokenfortests1234"), base_url=BASE,
    )


def _form(req) -> dict[str, str]:
    raw = req.read().decode("utf-8")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def _row(out_id="14885654", money=21500, r="liabilities_x80", **kw):
    base = {
        "out_id": out_id, "entry_date": "20260519", "money": money,
        "left": "", "right": "하나카드(2*9*)", "r3": "쿠팡(쿠페이)",
        "r5": "21:54", "raw": "하나카드(2*9*) 21500 쿠팡(쿠페이) 21:54",
        "detail": "쿠팡[쿠페이]", "r": r,
    }
    base.update(kw)
    return base


# ---- pure helpers -------------------------------------------------------

def test_parse_counter_account():
    assert parse_counter_account("liabilities_x80") == ("liabilities", "x80")
    assert parse_counter_account("assets_x4") == ("assets", "x4")


def test_parse_counter_account_malformed():
    assert parse_counter_account("x80") == ("", "")
    assert parse_counter_account("") == ("", "")


def test_staged_item_text_prefers_detail():
    assert staged_item_text(_row()) == "쿠팡[쿠페이]"
    assert staged_item_text(_row(detail="")) == "쿠팡(쿠페이)"  # r3 fallback
    assert staged_item_text({"raw": "원문만"}) == "원문만"


def test_build_entry_maps_sides():
    e = build_entry(_row(), l_account="expenses", l_account_id="x50")
    assert e["l_account"] == "expenses" and e["l_account_id"] == "x50"
    assert e["r_account"] == "liabilities" and e["r_account_id"] == "x80"
    assert e["money"] == 21500
    assert e["entry_date"] == "20260519"
    assert e["item"] == "쿠팡[쿠페이]"
    assert e["out_id"] == "14885654"


def test_build_entry_custom_item_and_memo():
    e = build_entry(
        _row(), l_account="expenses", l_account_id="x50",
        item="간식", memo="테스트",
    )
    assert e["item"] == "간식" and e["memo"] == "테스트"


# ---- OutsideClient (respx) ---------------------------------------------

@respx.mock
async def test_list_reads_with_empty_rows():
    route = respx.post(f"{BASE}/entries/outside.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"outdata": [_row(), _row("2")]}},
        ),
    )
    rows = await _client().list("s9046")
    assert len(rows) == 2
    form = _form(route.calls.last.request)
    # rows 가 빈 값이어야 '읽기'
    assert form["rows"] == ""
    assert form["section_id"] == "s9046"
    assert form["ids"] == "out_id"
    assert form["m"] == "n"


@respx.mock
async def test_list_all_single_page_stops():
    respx.post(f"{BASE}/entries/outside.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"outdata": [_row(), _row("2")]}},
        ),
    )
    rows = await _client().list_all("s9046")
    assert [r["out_id"] for r in rows] == ["14885654", "2"]


@respx.mock
async def test_confirm_posts_entries_and_del_ids():
    route = respx.post(f"{BASE}/entries.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"cnt": 1}}),
    )
    e = build_entry(_row(), l_account="expenses", l_account_id="x50")
    await _client().confirm("s9046", [e], ["14885654"])
    form = _form(route.calls.last.request)
    assert form["section_id"] == "s9046"
    assert form["del_ids"] == "14885654"
    assert '"l_account_id": "x50"' in form["entries"]
    assert '"r_account_id": "x80"' in form["entries"]


@respx.mock
async def test_delete_sends_empty_entries():
    route = respx.post(f"{BASE}/entries.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}}),
    )
    await _client().delete("s9046", ["14885654"])
    form = _form(route.calls.last.request)
    assert form["del_ids"] == "14885654"
    assert form["entries"] == "[]"


@respx.mock
async def test_empty_calls_empty_outside():
    route = respx.post(f"{BASE}/entries/empty_outside.json").mock(
        return_value=Response(200, json={"code": 200, "results": {}}),
    )
    await _client().empty("s9046")
    assert _form(route.calls.last.request)["section_id"] == "s9046"


@respx.mock
async def test_non_200_raises():
    respx.post(f"{BASE}/entries/outside.json").mock(
        return_value=Response(
            200, json={"code": 400, "message": "section_id required"},
        ),
    )
    try:
        await _client().list("s9046")
        assert False, "expected OutsideError"
    except OutsideError as e:
        assert "400" in str(e)
