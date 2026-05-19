"""DuplicateScanScreen + entries.py 의 "입력 → 중복 거래 검사" wiring 테스트.

CL #52957+ — 사용자 요청: 지난 3년 거래 일괄 스캔, cluster 마다 ✓/✗ toggle
로 삭제/보존 선택, Enter 로 다음 cluster.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError
from whooing_tui.screens.duplicate_scan import DuplicateScanScreen
from whooing_tui.screens.entries import EntriesScreen


class FakeClient:
    """3년치 거래에 중복 cluster 두 개 — identical + very_likely (swap)."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x21", "title": "교통비"},
            ],
        }
        if entries is not None:
            self._entries = entries
        else:
            self._entries = [
                # cluster A — identical 2건.
                {"entry_id": "a1", "entry_date": "20260510", "money": 10000,
                 "l_account_id": "x20", "r_account_id": "x11", "item": "스타벅스"},
                {"entry_id": "a2", "entry_date": "20260510", "money": 10000,
                 "l_account_id": "x20", "r_account_id": "x11", "item": "스타벅스"},
                # cluster B — very_likely (swapped accounts).
                {"entry_id": "b1", "entry_date": "20260201", "money": 5000,
                 "l_account_id": "x20", "r_account_id": "x11", "item": "버스"},
                {"entry_id": "b2", "entry_date": "20260201", "money": 5000,
                 "l_account_id": "x11", "r_account_id": "x20", "item": "버스"},
                # lonely — 어디에도 안 끼는 거래.
                {"entry_id": "z", "entry_date": "20250101", "money": 99999,
                 "l_account_id": "x21", "r_account_id": "x11", "item": "월세"},
            ]
        self.delete_calls: list[dict[str, Any]] = []
        self.delete_error: ToolError | None = None
        self.list_entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self) -> list[dict[str, Any]]:
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        self.list_entries_calls.append((section_id, start_date, end_date))
        return list(self._entries)

    async def delete_entry(self, *, section_id, entry_id) -> dict[str, Any]:
        if self.delete_error is not None:
            raise self.delete_error
        self.delete_calls.append({"section_id": section_id, "entry_id": entry_id})
        self._entries = [e for e in self._entries if e.get("entry_id") != entry_id]
        return {}


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def _open_entries(app) -> EntriesScreen:
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1",
        timeout=3.0,
    )
    await _wait_for(
        lambda: app.screen.last_entry_count >= 1, timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


# ----------------------------------------------------------------------
# Menu wiring
# ----------------------------------------------------------------------


def test_scan_duplicates_in_input_menu():
    """"입력" 메뉴에 "중복 거래 검사…" 항목이 있는지 + action_id."""
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    input_menu = next(m for m in menus if m.name == "입력")
    labels = [it.label for it in input_menu.items]
    assert any("중복 거래 검사" in lab for lab in labels)
    ids = [it.action_id for it in input_menu.items]
    assert "scan_duplicates" in ids


# ----------------------------------------------------------------------
# Screen behaviour
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_menu_popup_enter_press_dispatches_scan_duplicates():
    """실제 키보드 Enter 로 '중복 거래 검사' 선택 → DuplicateScanScreen 떠야.

    CL #52968: 사용자 보고 "중복 거래 검사를 눌러도 아무 일도 안 일어납니다"
    의 회귀. 단순 popup.dismiss(action_id) 직접 호출은 통과하지만 실제 키
    이벤트 (OptionList → OptionSelected → dismiss) 경로도 검증.
    """
    from whooing_tui.widgets.menubar import MenuPopup
    from textual.widgets import OptionList
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        assert isinstance(es, EntriesScreen)
        es.action_open_menu()
        await _wait_for(
            lambda: isinstance(app.screen, MenuPopup), timeout=2.0,
        )
        # "입력" 으로 이동.
        menus = es._build_menus()
        target = next(i for i, m in enumerate(menus) if m.name == "입력")
        for _ in range(target):
            await pilot.press("right")
            await pilot.pause()
        popup = app.screen
        assert isinstance(popup, MenuPopup)
        assert popup.spec.name == "입력"
        # OptionList 의 highlighted 를 "scan_duplicates" 로 옮기고 Enter.
        ol = popup.query_one("#menupopup_list", OptionList)
        idx = next(
            i for i, opt in enumerate(popup.spec.items)
            if opt.action_id == "scan_duplicates"
        )
        ol.highlighted = idx
        await pilot.pause()
        await pilot.press("enter")
        # DuplicateScanScreen 이 떠야.
        ok = await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=5.0,
        )
        assert ok, (
            f"DuplicateScanScreen not pushed. "
            f"Current: {type(app.screen).__name__}, "
            f"status={es.last_status!r}"
        )


@pytest.mark.asyncio
async def test_menu_popup_dispatches_scan_duplicates():
    """메뉴 popup → '중복 거래 검사' 선택 → DuplicateScanScreen 떠야.

    CL #52963 직후 사용자 보고: "중복 거래 검사를 눌러도 아무 일도 안
    일어납니다." direct action_scan_duplicates 호출은 통과 (다른 테스트),
    하지만 menu popup 의 dismiss(action_id) → _dispatch_menu_action 경로가
    실제로 worker 까지 도달하는지 검증.
    """
    from whooing_tui.widgets.menubar import MenuPopup
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        assert isinstance(es, EntriesScreen)
        # 메뉴바 진입 — _open_menu_loop_async 가 worker 로 popup 띄움.
        es.action_open_menu()
        await _wait_for(
            lambda: isinstance(app.screen, MenuPopup), timeout=2.0,
        )
        popup = app.screen
        assert isinstance(popup, MenuPopup)
        # "입력" 메뉴까지 ← / → 로 이동.
        menus = es._build_menus()
        target = next(i for i, m in enumerate(menus) if m.name == "입력")
        cur = next(i for i, m in enumerate(menus) if m.name == popup.spec.name)
        while cur != target:
            direction = "right" if cur < target else "left"
            popup.dismiss(("nav", direction))
            await _wait_for(
                lambda: isinstance(app.screen, MenuPopup)
                and app.screen is not popup, timeout=2.0,
            )
            popup = app.screen
            cur = next(i for i, m in enumerate(menus) if m.name == popup.spec.name)
        assert popup.spec.name == "입력"
        # "중복 거래 검사" 선택 = popup 이 action_id 로 dismiss.
        popup.dismiss("scan_duplicates")
        # worker → list_entries → find_clusters → push DuplicateScanScreen.
        ok = await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=5.0,
        )
        assert ok, (
            f"DuplicateScanScreen not pushed. "
            f"Current: {type(app.screen).__name__}, "
            f"status={es.last_status!r}"
        )


@pytest.mark.asyncio
async def test_scan_finds_two_clusters_and_opens_screen():
    """fake 3년 ledger 안 cluster 2개 — DuplicateScanScreen 이 떠야."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        assert isinstance(es, EntriesScreen)
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, DuplicateScanScreen)
        # cluster 2개 (identical + very_likely).
        assert len(scr._clusters) == 2
        # identical 이 먼저 정렬.
        assert scr._clusters[0].verdict == "identical"
        assert scr._clusters[1].verdict == "very_likely"
        # 첫 cluster 의 기본 mark — keep_suggestion 만 False (보존),
        # 다른 1건은 True (삭제).
        marks = scr._marks[0]
        keep = scr._clusters[0].keep_suggestion
        for eid, m in marks.items():
            assert m == (eid != keep)


@pytest.mark.asyncio
async def test_scan_no_duplicates_status_message():
    """중복 후보 0건 — 화면 안 띄우고 status 만 보고."""
    fake = FakeClient(entries=[
        {"entry_id": "x", "entry_date": "20260510", "money": 1000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "A"},
        {"entry_id": "y", "entry_date": "20260201", "money": 2000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "B"},
    ])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        assert isinstance(es, EntriesScreen)
        es.action_scan_duplicates()
        # worker 가 status 까지 진행하도록 대기.
        await _wait_for(
            lambda: "중복 후보 없음" in es.last_status, timeout=3.0,
        )
        # DuplicateScanScreen 은 push 되지 않아야.
        assert not isinstance(app.screen, DuplicateScanScreen)


@pytest.mark.asyncio
async def test_scan_confirm_deletes_marked_entries_and_advances():
    """Enter → 현재 cluster 의 ✓ 표시 거래 삭제 + 다음 cluster 이동."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        # 첫 cluster 의 keep_suggestion 이 아닌 entry_id 가 삭제 대상.
        c0 = scr._clusters[0]
        keep0 = c0.keep_suggestion
        expected_deleted = [
            str(e.get("entry_id")) for e in c0.entries
            if str(e.get("entry_id")) != keep0
        ]
        assert expected_deleted  # 적어도 1건 있어야.
        # 사용자가 Enter → 삭제 + 다음 cluster.
        scr.action_confirm()
        ok = await _wait_for(
            lambda: scr._idx == 1, timeout=3.0,
        )
        assert ok
        # 호출 검증.
        deleted_ids = [c["entry_id"] for c in fake.delete_calls]
        assert set(deleted_ids) == set(expected_deleted)


@pytest.mark.asyncio
async def test_scan_toggle_delete_flips_mark():
    """Space — 현재 row 의 mark toggle."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        # 첫 cluster, cursor row 0 의 mark.
        from textual.widgets import DataTable
        t = scr.query_one("#scan-table", DataTable)
        t.move_cursor(row=0)
        await pilot.pause()
        first_eid = str(scr._clusters[0].entries[0].get("entry_id"))
        before = scr._marks[0][first_eid]
        scr.action_toggle_delete()
        after = scr._marks[0][first_eid]
        assert after != before


@pytest.mark.asyncio
async def test_scan_skip_moves_next_without_deleting():
    """n / → — 삭제 없이 다음 cluster."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        assert scr._idx == 0
        scr.action_next_cluster()
        assert scr._idx == 1
        assert fake.delete_calls == []


@pytest.mark.asyncio
async def test_scan_prev_returns_to_previous_cluster():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        scr.action_next_cluster()
        assert scr._idx == 1
        scr.action_prev_cluster()
        assert scr._idx == 0


@pytest.mark.asyncio
async def test_scan_esc_no_changes_returns_false():
    fake = FakeClient(entries=[
        {"entry_id": "a", "entry_date": "20260510", "money": 1000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "x"},
        {"entry_id": "b", "entry_date": "20260510", "money": 1000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "x"},
    ])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        scr.action_cancel()
        await pilot.pause()
        assert fake.delete_calls == []


@pytest.mark.asyncio
async def test_scan_confirm_with_no_marks_shows_error():
    """모든 ✓ 를 해제한 cluster 에서 Enter — 안내만, 삭제 안 함."""
    fake = FakeClient(entries=[
        {"entry_id": "a", "entry_date": "20260510", "money": 1000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "x"},
        {"entry_id": "b", "entry_date": "20260510", "money": 1000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "x"},
    ])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        # 모든 mark 를 False (보존).
        for eid in scr._marks[0]:
            scr._marks[0][eid] = False
        scr.action_confirm()
        await pilot.pause()
        assert "삭제 대상이 없습니다" in scr.last_status
        assert fake.delete_calls == []


@pytest.mark.asyncio
async def test_scan_progress_counter_format():
    """진행률 표시: 'N / T  ·  verdict'."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        scr = app.screen
        await pilot.pause()
        from textual.widgets import Static
        prog = scr.query_one("#scan-progress", Static)
        text = str(prog.render())
        # N / T 형식.
        assert "1 / 2" in text or "1/2" in text
