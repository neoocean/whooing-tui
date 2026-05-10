"""tests/_helpers.py — shared FakeWhooingClient / wait_for / make_fake_p4.

CL #51157+ (review C7+C8+C9). 본 파일은 helper 자체의 sanity 만 — 기존
테스트는 자체 _FakeClient 유지 (기존 working code, 점진적 채택).
"""

from __future__ import annotations

import asyncio
import pytest

from tests._helpers import FakeWhooingClient, make_fake_p4, wait_for


# ---- wait_for ------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_returns_true_when_predicate_met():
    counter = {"n": 0}

    async def increment():
        for _ in range(5):
            await asyncio.sleep(0.01)
            counter["n"] += 1

    task = asyncio.create_task(increment())
    ok = await wait_for(lambda: counter["n"] >= 3, timeout=1.0)
    await task
    assert ok is True


@pytest.mark.asyncio
async def test_wait_for_returns_false_on_timeout():
    ok = await wait_for(lambda: False, timeout=0.1, interval=0.01)
    assert ok is False


# ---- FakeWhooingClient ---------------------------------------------------


@pytest.mark.asyncio
async def test_fake_client_default_section_and_accounts():
    fake = FakeWhooingClient()
    sections = await fake.list_sections()
    assert sections == [{"section_id": "s1", "title": "main"}]
    accounts = await fake.list_accounts("s1")
    assert "expenses" in accounts
    assert accounts["expenses"][0]["account_id"] == "x20"


@pytest.mark.asyncio
async def test_fake_client_records_create_calls():
    fake = FakeWhooingClient()
    response = await fake.create_entry(
        section_id="s1", l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x11",
        money=1000, item="x", memo="", entry_date="20260510",
    )
    assert response["entry_id"].startswith("e_new_")
    assert len(fake.create_entry_calls) == 1
    assert fake.create_entry_calls[0]["money"] == 1000


@pytest.mark.asyncio
async def test_fake_client_monthly_endpoints():
    fake = FakeWhooingClient(monthly_rows=[
        {"id": "m1", "target_day": 25, "money": 50000},
    ])
    rows = await fake.list_monthly(section_id="s1")
    assert rows[0]["id"] == "m1"
    new = await fake.create_monthly(
        section_id="s1", target_day=15, money=10000,
        l_account="expenses", l_account_id="x20",
        r_account="assets", r_account_id="x11", item="", memo="",
    )
    assert new["id"] == "m2"
    await fake.delete_monthly(section_id="s1", monthly_id="m1")
    assert fake.delete_monthly_calls == [("s1", "m1")]
    remaining = await fake.list_monthly(section_id="s1")
    assert [r["id"] for r in remaining] == ["m2"]


@pytest.mark.asyncio
async def test_fake_client_budget_setters():
    fake = FakeWhooingClient()
    await fake.set_budget(
        section_id="s1", account="expenses", account_id="x20", amount=200000,
    )
    assert fake.set_budget_calls == [{
        "section_id": "s1", "account": "expenses",
        "account_id": "x20", "amount": 200000,
    }]


@pytest.mark.asyncio
async def test_fake_client_entries_error_propagates():
    from whooing_tui.models import ToolError
    fake = FakeWhooingClient(entries_error=ToolError("X", "fail"))
    with pytest.raises(ToolError):
        await fake.list_entries("s1", "20260101", "20260131")


# ---- make_fake_p4 --------------------------------------------------------


def test_make_fake_p4_basic_returns_executable(tmp_path):
    p = make_fake_p4(tmp_path)
    assert p.exists()
    import os
    assert os.access(p, os.X_OK)
    # 모든 명령 exit 0.
    import subprocess
    r = subprocess.run([str(p), "info"], capture_output=True)
    assert r.returncode == 0


def test_make_fake_p4_with_log(tmp_path):
    log = tmp_path / "p4.log"
    p = make_fake_p4(tmp_path, log_file=log)
    import subprocess
    subprocess.run([str(p), "where", "/foo"], capture_output=True)
    subprocess.run([str(p), "submit", "-d", "x", "/foo"], capture_output=True)
    lines = log.read_text().splitlines()
    assert any(l.startswith("where ") for l in lines)
    assert any(l.startswith("submit ") for l in lines)


def test_make_fake_p4_where_returns_1(tmp_path):
    """where 만 1 (매핑 외 시뮬), 다른 명령은 0."""
    p = make_fake_p4(tmp_path, where_returns=1, other_returns=0)
    import subprocess
    r1 = subprocess.run([str(p), "where", "/x"], capture_output=True)
    r2 = subprocess.run([str(p), "info"], capture_output=True)
    assert r1.returncode == 1
    assert r2.returncode == 0
