"""ReceiptAttachScreen — 매칭 / 첨부 / 제안 wizard 회귀.

CL #51128+. extractor 자체는 core/tests/test_receipt_extractor.py 가 검증;
본 테스트는 TUI 흐름에 한정 — find_candidate_entries (matcher) + screen
의 candidate 표시 + a/n 키 분기.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.receipt_attach import (
    ReceiptAttachScreen,
    find_candidate_entries,
)


# ---- find_candidate_entries unit ---------------------------------------


def _e(eid: str, date: str, money: int, **kw: Any) -> dict[str, Any]:
    base = {"entry_id": eid, "entry_date": date, "money": money}
    base.update(kw)
    return base


def test_match_exact_amount_and_date():
    entries = [
        _e("e1", "20260510", 12000),
        _e("e2", "20260510", 5000),
    ]
    out = find_candidate_entries(entries, date="20260510", amount=12000)
    assert [e["entry_id"] for e in out] == ["e1"]


def test_match_within_window_days():
    entries = [
        _e("e1", "20260505", 12000),  # -5 days
        _e("e2", "20260515", 12000),  # +5 days
        _e("e3", "20260520", 12000),  # +10 days — out of window
    ]
    out = find_candidate_entries(
        entries, date="20260510", amount=12000, window_days=7,
    )
    assert sorted(e["entry_id"] for e in out) == ["e1", "e2"]


def test_match_no_amount_returns_empty():
    """amount 없이는 매칭 불가."""
    entries = [_e("e1", "20260510", 12000)]
    assert find_candidate_entries(entries, date="20260510", amount=None) == []


def test_match_no_date_ignores_window():
    """date None 이면 amount 만 일치 — 윈도우 무시."""
    entries = [
        _e("e1", "20260101", 12000),
        _e("e2", "20261231", 12000),
        _e("e3", "20260510", 5000),
    ]
    out = find_candidate_entries(entries, date=None, amount=12000)
    assert sorted(e["entry_id"] for e in out) == ["e1", "e2"]


def test_match_skips_non_integer_money():
    entries = [
        _e("e1", "20260510", "abc"),   # type: ignore[arg-type]
        _e("e2", "20260510", 12000),
    ]
    out = find_candidate_entries(entries, date="20260510", amount=12000)
    assert [e["entry_id"] for e in out] == ["e2"]


def test_match_handles_subindex_dates():
    """후잉 entry_date 가 '20260510.0001' 같은 sub-index 포함이어도 매칭."""
    entries = [_e("e1", "20260510.0002", 12000)]
    out = find_candidate_entries(entries, date="20260510", amount=12000)
    assert [e["entry_id"] for e in out] == ["e1"]


def test_match_empty_entries():
    assert find_candidate_entries([], date="20260510", amount=100) == []


# ---- Screen integration ------------------------------------------------


class _FakeClient:
    def __init__(self, entries: list[dict[str, Any]] | None = None):
        self._entries = entries or []
        self.list_entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self):
        return [{"section_id": "s1", "title": "main"}]

    async def list_accounts(self, section_id: str):
        return {"assets": [{"account_id": "x11", "title": "현금"}]}

    async def list_entries(self, section_id, start_date, end_date):
        self.list_entries_calls.append((section_id, start_date, end_date))
        return list(self._entries)


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_receipt_screen_extracts_and_finds_zero_candidates(tmp_path):
    """빈 PDF (텍스트 추출 0) → receipt.amount/date None → candidates 0."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"")  # 텍스트 없는 PDF — extractor 가 None 반환.

    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        screen = ReceiptAttachScreen(fake, app.session, str(pdf))
        await app.push_screen(screen)
        await _wait_for(lambda: screen.receipt is not None, timeout=3.0)
        assert screen.receipt is not None
        # 빈 PDF — date/amount 둘 다 None.
        assert screen.receipt.amount is None
        assert screen.candidates == []


@pytest.mark.asyncio
async def test_receipt_screen_action_attach_no_candidate_warns(tmp_path):
    """후보 0인 상태에서 `a` 누르면 경고 status — push pop 없음."""
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"")
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        screen = ReceiptAttachScreen(fake, app.session, str(pdf))
        await app.push_screen(screen)
        await _wait_for(lambda: screen.receipt is not None, timeout=3.0)
        screen.action_attach_selected()
        await pilot.pause()
        # 화면 그대로.
        assert isinstance(app.screen, ReceiptAttachScreen)


def test_menu_includes_attach_receipt_item():
    """메뉴 정의에 attach_receipt 항목 포함 — wiring 안전망."""
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "attach_receipt" for _, action in flat), (
        f"메뉴 항목 누락: {flat}"
    )
