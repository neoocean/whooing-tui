"""sqlite 캐시 + CachedWhooingClient 단위/통합 테스트."""

from __future__ import annotations

import time

import pytest
import respx
from httpx import Response

from whooing_tui.auth import WhooingAuth
from whooing_tui.cache import CacheStore
from whooing_tui.client import CachedWhooingClient, WhooingClient


# ---- CacheStore 단위 ----------------------------------------------------

def test_accounts_roundtrip_in_memory():
    store = CacheStore(":memory:")
    assert store.get_accounts("s1") is None
    store.put_accounts("s1", {"assets": [{"account_id": "x11", "title": "현금"}]})
    out = store.get_accounts("s1")
    assert out == {"assets": [{"account_id": "x11", "title": "현금"}]}
    store.close()


def test_accounts_ttl_expires():
    store = CacheStore(":memory:")
    store.put_accounts("s1", {"assets": []})
    # max_age_sec=0 → 즉시 만료
    assert store.get_accounts("s1", max_age_sec=0) is None
    # max_age_sec=-1 → TTL 무시 (영구)
    assert store.get_accounts("s1", max_age_sec=-1) == {"assets": []}


def test_invalidate_accounts_one_section():
    store = CacheStore(":memory:")
    store.put_accounts("s1", {"a": 1})
    store.put_accounts("s2", {"b": 2})
    store.invalidate_accounts("s1")
    assert store.get_accounts("s1") is None
    assert store.get_accounts("s2") == {"b": 2}


def test_invalidate_accounts_all():
    store = CacheStore(":memory:")
    store.put_accounts("s1", {"a": 1})
    store.put_accounts("s2", {"b": 2})
    store.invalidate_accounts(None)
    assert store.get_accounts("s1") is None
    assert store.get_accounts("s2") is None


def test_entries_roundtrip_window_keyed():
    store = CacheStore(":memory:")
    rows = [{"entry_id": "e1", "money": 1000}]
    store.put_entries("s1", "20260501", "20260510", rows)
    assert store.get_entries("s1", "20260501", "20260510") == rows
    # 다른 윈도우는 별개 캐시
    assert store.get_entries("s1", "20260501", "20260511") is None


def test_entries_invalidate_drops_all_windows_for_section():
    store = CacheStore(":memory:")
    store.put_entries("s1", "20260501", "20260510", [{"e": 1}])
    store.put_entries("s1", "20260511", "20260520", [{"e": 2}])
    store.put_entries("s2", "20260501", "20260510", [{"e": 3}])
    store.invalidate_entries("s1")
    assert store.get_entries("s1", "20260501", "20260510") is None
    assert store.get_entries("s1", "20260511", "20260520") is None
    assert store.get_entries("s2", "20260501", "20260510") == [{"e": 3}]


def test_entries_invalidate_targeted_by_entry_date():
    """감사 2-E: entry_date 가 주어지면 그 날짜를 *포함하는* 윈도우만 제거."""
    store = CacheStore(":memory:")
    store.put_entries("s1", "20260501", "20260531", [{"e": "may"}])    # 포함
    store.put_entries("s1", "20260601", "20260630", [{"e": "jun"}])    # 미포함
    store.put_entries("s2", "20260501", "20260531", [{"e": "other"}])  # 타 섹션
    # 5/15 거래 생성 → 5월 윈도우만 무효화.
    store.invalidate_entries("s1", entry_date="20260515")
    assert store.get_entries("s1", "20260501", "20260531") is None     # 제거됨
    assert store.get_entries("s1", "20260601", "20260630") == [{"e": "jun"}]  # 유지
    assert store.get_entries("s2", "20260501", "20260531") == [{"e": "other"}]


def test_entries_invalidate_targeted_handles_subindex_date():
    store = CacheStore(":memory:")
    store.put_entries("s1", "20260601", "20260630", [{"e": "jun"}])
    # entry_date 에 sub-index(.NNNN)가 붙어도 8자리 prefix 로 비교.
    store.invalidate_entries("s1", entry_date="20260615.0003")
    assert store.get_entries("s1", "20260601", "20260630") is None


def test_stats():
    store = CacheStore(":memory:")
    assert store.stats() == {"account_rows": 0, "entries_rows": 0}
    store.put_accounts("s1", {})
    store.put_entries("s1", "a", "b", [])
    assert store.stats() == {"account_rows": 1, "entries_rows": 1}


def test_persistence_across_connections(tmp_path):
    db = tmp_path / "cache.sqlite"
    s1 = CacheStore(db)
    s1.put_accounts("s1", {"a": 1})
    s1.close()
    s2 = CacheStore(db)
    assert s2.get_accounts("s1") == {"a": 1}
    s2.close()


# ---- CachedWhooingClient 통합 -------------------------------------------

def _make_cached() -> tuple[CachedWhooingClient, CacheStore]:
    inner = WhooingClient(
        WhooingAuth(token="__eyJhfaketokenfortests1234"),
        base_url="https://whooing.com/api",
    )
    store = CacheStore(":memory:")
    return CachedWhooingClient(inner, store), store


@respx.mock
async def test_list_accounts_cache_hit_skips_http():
    route = respx.get("https://whooing.com/api/accounts.json").mock(
        return_value=Response(
            200,
            json={
                "code": 200,
                "results": {"assets": [{"account_id": "x11", "title": "현금"}]},
            },
        )
    )
    cc, store = _make_cached()
    a1 = await cc.list_accounts("s1")
    a2 = await cc.list_accounts("s1")
    assert a1 == a2
    # 두 번째 호출은 캐시 hit — http 는 1번만 일어남
    assert route.call_count == 1
    assert store.stats()["account_rows"] == 1


@respx.mock
async def test_list_entries_cache_hit_skips_http():
    rows = [{"entry_id": "e1", "money": 1000, "entry_date": "20260510"}]
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"reports": [], "rows": rows}},
        )
    )
    cc, store = _make_cached()
    out1 = await cc.list_entries("s1", "20260501", "20260510")
    out2 = await cc.list_entries("s1", "20260501", "20260510")
    assert out1 == out2 == rows
    # respx route 의 call_count 는 위에서 직접 추적 — 2번째 호출이
    # cached 면 entries_rows 가 1.
    assert store.stats()["entries_rows"] == 1


@respx.mock
async def test_create_entry_invalidates_entries_cache():
    rows = [{"entry_id": "e1", "money": 1000, "entry_date": "20260510"}]
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"reports": [], "rows": rows}},
        )
    )
    respx.post("https://whooing.com/api/entries.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"entry_id": "new"}})
    )
    cc, store = _make_cached()
    await cc.list_entries("s1", "20260501", "20260510")
    assert store.stats()["entries_rows"] == 1
    # 감사 2-E: entry_date 가 캐시 윈도우 [0501,0510] 안이면 그 윈도우 무효화.
    await cc.create_entry(
        section_id="s1",
        l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x11",
        money=1000,
        entry_date="20260505",
    )
    assert store.stats()["entries_rows"] == 0


@respx.mock
async def test_create_entry_outside_window_keeps_cache():
    """감사 2-E: 윈도우 밖 날짜로 생성하면 그 윈도우는 유지 (선택 무효화)."""
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"rows": []}})
    )
    respx.post("https://whooing.com/api/entries.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"entry_id": "new"}})
    )
    cc, store = _make_cached()
    await cc.list_entries("s1", "20260501", "20260510")  # 5월 초 윈도우.
    assert store.stats()["entries_rows"] == 1
    await cc.create_entry(
        section_id="s1",
        l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x11",
        money=1000,
        entry_date="20260801",   # 윈도우 밖(8월).
    )
    assert store.stats()["entries_rows"] == 1   # 유지.


@respx.mock
async def test_invalidate_section_clears_both_accounts_and_entries():
    respx.get("https://whooing.com/api/accounts.json").mock(
        return_value=Response(200, json={"code": 200, "results": {"assets": []}}),
    )
    respx.get("https://whooing.com/api/entries.json").mock(
        return_value=Response(
            200, json={"code": 200, "results": {"reports": [], "rows": []}},
        )
    )
    cc, store = _make_cached()
    await cc.list_accounts("s1")
    await cc.list_entries("s1", "20260501", "20260510")
    assert store.stats() == {"account_rows": 1, "entries_rows": 1}
    cc.invalidate_section("s1")
    assert store.stats() == {"account_rows": 0, "entries_rows": 0}
