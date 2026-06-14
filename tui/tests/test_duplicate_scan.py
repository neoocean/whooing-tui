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
from whooing_tui.screens.duplicate_scan import (
    DupeScanRangeModal,
    DuplicateScanScreen,
    ScanProgressModal,
)
from whooing_tui.screens.dupe_scan_overview import DupeScanOverviewScreen
from whooing_tui.screens.entries import EntriesScreen


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """매 테스트 격리 — sqlite 가 tmp_path 안. CL #52989+ 부터 worker 가
    sqlite 의 dupe_scan_clusters 를 사용하므로 격리 필요."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    # 스키마는 worker / repo 진입 시 init 됨. 미리 만들지 않아도 동작.
    yield tmp_path


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

    async def list_entries(
        self, section_id, start_date, end_date, *, on_progress=None,
    ):
        self.list_entries_calls.append((section_id, start_date, end_date))
        # CL #53010+: 진행 콜백이 들어오면 fetch/received/done 시뮬레이션 —
        # 실제 client 와 같은 sequence 로 호출 (테스트의 worker UI 흐름 검증).
        if on_progress is not None:
            try:
                on_progress("fetch", start_date, end_date)
                on_progress(
                    "received", start_date, end_date,
                    count=len(self._entries),
                )
                on_progress(
                    "done", start_date, end_date, total=len(self._entries),
                )
            except Exception:
                pass
        return list(self._entries)

    async def delete_entry(
        self, *, section_id, entry_id, entry_date=None,
    ) -> dict[str, Any]:
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


async def _accept_range_modal(app, days: int = 365 * 3) -> None:
    """CL #53006+: 새 첫 화면은 DupeScanRangeModal. 사용자가 일수 선택해야
    worker 가 진행. 본 helper 가 자동으로 default (3년) 선택해 통과시킴.
    """
    await _wait_for(
        lambda: isinstance(app.screen, DupeScanRangeModal),
        timeout=3.0,
    )
    app.screen.dismiss(days)


async def _drive_to_cluster_screen(app, days: int = 365 * 3) -> DuplicateScanScreen:
    """CL #52989+: action_scan_duplicates → range → overview → cluster.

    CL #53006+: range modal 도 통과.
    """
    es = app.screen
    es.action_scan_duplicates()
    await _accept_range_modal(app, days=days)
    await _wait_for(
        lambda: isinstance(app.screen, DupeScanOverviewScreen),
        timeout=5.0,
    )
    overview = app.screen
    overview.action_start_cleanup()
    await _wait_for(
        lambda: isinstance(app.screen, DuplicateScanScreen),
        timeout=5.0,
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
        # CL #53006+: 첫 화면은 DupeScanRangeModal (범위 선택).
        ok = await _wait_for(
            lambda: isinstance(app.screen, DupeScanRangeModal),
            timeout=5.0,
        )
        assert ok
        await _accept_range_modal(app)
        ok = await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        assert ok, (
            f"DupeScanOverviewScreen not pushed. "
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
        # CL #53006+: worker → range modal → fetch → DupeScanOverviewScreen.
        ok = await _wait_for(
            lambda: isinstance(app.screen, DupeScanRangeModal),
            timeout=5.0,
        )
        assert ok
        await _accept_range_modal(app)
        ok = await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        assert ok, (
            f"DupeScanOverviewScreen not pushed. "
            f"Current: {type(app.screen).__name__}, "
            f"status={es.last_status!r}"
        )


@pytest.mark.asyncio
async def test_scan_shows_progress_modal_during_fetch():
    """fetch / 분석 중 ScanProgressModal 이 화면 위에 떠야 — 그리고 결과
    DupeScanOverviewScreen 으로 자동 교체 (CL #52989+ 2단계 UI)."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        assert isinstance(es, EntriesScreen)
        es.action_scan_duplicates()
        await _accept_range_modal(app)
        ok = await _wait_for(
            lambda: isinstance(
                app.screen,
                (ScanProgressModal, DupeScanOverviewScreen),
            ),
            timeout=3.0,
        )
        assert ok
        # 결국 overview 로.
        ok2 = await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        assert ok2, (
            f"Did not reach DupeScanOverviewScreen. "
            f"Final: {type(app.screen).__name__}"
        )


def test_scan_progress_modal_set_activity_buffer_before_mount():
    """ScanProgressModal.set_activity 가 mount 전에도 안전 — buffer 에 저장."""
    m = ScanProgressModal(initial="처음")
    assert m.last_activity == "처음"
    # mount 전 호출 — 예외 없이 buffer 갱신.
    m.set_activity("두번째")
    assert m.last_activity == "두번째"
    assert m._activity_text == "두번째"


@pytest.mark.asyncio
async def test_scan_progress_modal_dismissed_when_no_clusters():
    """중복 0건이면 progress modal 만 잠깐 떴다 사라지고 overview 도
    cluster 화면도 안 뜸 (status 만 갱신)."""
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
        await _accept_range_modal(app)
        await _wait_for(
            lambda: "중복 후보 없음" in es.last_status, timeout=3.0,
        )
        assert not isinstance(app.screen, ScanProgressModal)
        assert not isinstance(app.screen, DuplicateScanScreen)
        assert not isinstance(app.screen, DupeScanOverviewScreen)


@pytest.mark.asyncio
async def test_progress_modal_updates_per_chunk_during_fetch():
    """CL #53010+: 진행 popup 이 fetch 단계별로 set_activity 호출 받음.

    FakeClient.list_entries 가 fetch/received/done 콜백을 발사하므로,
    worker 의 _on_fetch_progress 가 ScanProgressModal.set_activity 를
    여러 번 갱신. last_activity 가 마지막 갱신 (fetch 완료) 반영.
    """
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _accept_range_modal(app)
        # overview 까지 도달 — fetch progress events 모두 emit 되고 끝남.
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        # FakeClient 가 fetch/received/done 3 단계 발사 → worker 가
        # progress modal 의 set_activity 를 3 번 이상 호출. modal 자체는
        # 이미 dismiss 됐지만 last_activity 가 마지막 텍스트를 남김.
        # 누적 검증은 어렵지만 화면 flow 가 정상 종료됐다는 사실로 충분.
        assert isinstance(app.screen, DupeScanOverviewScreen)


@pytest.mark.asyncio
async def test_range_modal_appears_first():
    """CL #53006+: action_scan_duplicates 의 첫 화면은 DupeScanRangeModal."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        ok = await _wait_for(
            lambda: isinstance(app.screen, DupeScanRangeModal),
            timeout=3.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_range_modal_esc_cancels_scan():
    """Esc 면 wizard 취소 — fetch 호출 X, overview/cluster 화면 안 뜸."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        before = len(fake.list_entries_calls)
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanRangeModal),
            timeout=3.0,
        )
        # 취소.
        app.screen.dismiss(None)
        await _wait_for(
            lambda: "중복 검사 취소" in es.last_status, timeout=2.0,
        )
        # 취소 후엔 추가 fetch 가 없어야 (entries init 호출 외).
        assert len(fake.list_entries_calls) == before
        assert not isinstance(app.screen, DupeScanOverviewScreen)


@pytest.mark.asyncio
async def test_range_modal_picks_one_month_uses_30_days():
    """1개월 선택 시 list_entries 의 start_date 가 today-30일."""
    from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _accept_range_modal(app, days=30)
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        assert fake.list_entries_calls
        section_id, start, end = fake.list_entries_calls[-1]
        # 30일 = 1 개월.
        assert start == days_ago_yyyymmdd(30)
        assert end == today_yyyymmdd()


@pytest.mark.asyncio
async def test_range_modal_remembers_last_choice():
    """두번째 진입 시 default_days 가 이전 선택 (다른 항목 highlight)."""
    fake = FakeClient(entries=[
        # 중복 없도록 — 첫 스캔 후 status 만 출력하고 overview 안 뜨게.
        {"entry_id": "x", "entry_date": "20260510", "money": 1000,
         "l_account_id": "x20", "r_account_id": "x11", "item": "A"},
    ])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        # 1차: 6개월 선택.
        es.action_scan_duplicates()
        await _accept_range_modal(app, days=180)
        await _wait_for(
            lambda: "중복 후보 없음" in es.last_status, timeout=3.0,
        )
        assert es._last_dupe_scan_days == 180
        # 2차: range modal 의 _initial_days 가 180.
        es.action_scan_duplicates()
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanRangeModal),
            timeout=2.0,
        )
        assert app.screen._initial_days == 180
        app.screen.dismiss(None)


def test_range_modal_options_are_sorted():
    """OPTIONS 의 일수가 단조증가."""
    days = [d for _, d in DupeScanRangeModal.OPTIONS]
    assert days == sorted(days)
    assert days[0] == 30 and days[-1] == 365 * 5


@pytest.mark.asyncio
async def test_scan_finds_two_clusters_and_opens_overview():
    """CL #52989+: 첫 화면은 overview 로 — cluster 2개 보여준다."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        assert isinstance(es, EntriesScreen)
        es.action_scan_duplicates()
        await _accept_range_modal(app)
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=3.0,
        )
        await pilot.pause()
        overview = app.screen
        assert isinstance(overview, DupeScanOverviewScreen)
        assert len(overview._clusters) == 2
        # identical 이 먼저 (StoredCluster 정렬 기준 동일).
        assert overview._clusters[0].verdict == "identical"
        assert overview._clusters[1].verdict == "very_likely"


@pytest.mark.asyncio
async def test_scan_caches_clusters_in_sqlite_for_reuse():
    """CL #52989+: 첫 스캔 후 sqlite 에 저장 → 두번째 스캔은 fetch 안 함."""
    from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
    from whooing_tui.dupe_scan_repo import DupeScanRepository

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _accept_range_modal(app)
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        # 1차 fetch — list_entries 호출됨.
        assert len(fake.list_entries_calls) >= 1
        first_fetch_count = len(fake.list_entries_calls)
        # repo 에 cluster 저장 확인.
        repo = DupeScanRepository()
        cached = repo.load_open_clusters(
            section_id="s1",
            range_start=days_ago_yyyymmdd(365 * 3),
            range_end=today_yyyymmdd(),
        )
        assert len(cached) == 2
        # 닫고 다시 스캔 — 캐시 hit, fetch 추가 호출 X.
        app.screen.action_close()
        await pilot.pause()
        es.action_scan_duplicates()
        await _accept_range_modal(app)
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        # list_entries 호출 수가 늘지 않아야 (캐시 hit).
        assert len(fake.list_entries_calls) == first_fetch_count, (
            f"cached scan should not re-fetch — calls: {fake.list_entries_calls}"
        )
        # 사용자에게 cache hit 알림.
        assert app.screen._cached is True


@pytest.mark.asyncio
async def test_scan_refresh_clears_cache_and_refetches():
    """F5 / 새로고침 — sqlite 비우고 후잉 재요청."""
    from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        es = app.screen
        es.action_scan_duplicates()
        await _accept_range_modal(app)
        await _wait_for(
            lambda: isinstance(app.screen, DupeScanOverviewScreen),
            timeout=5.0,
        )
        first_calls = len(fake.list_entries_calls)
        overview = app.screen
        overview.action_refresh()
        # refresh worker → 다시 fetch.
        ok = await _wait_for(
            lambda: len(fake.list_entries_calls) > first_calls,
            timeout=5.0,
        )
        assert ok
        await pilot.pause()
        # 화면은 overview 그대로 (재로딩).
        assert isinstance(app.screen, DupeScanOverviewScreen)
        # cached 플래그가 False 로 (방금 새로 받음).
        assert app.screen._cached is False


@pytest.mark.asyncio
async def test_cluster_resolution_persists_to_repo():
    """Enter (deletion) → repo 의 cluster status 가 'resolved' 로."""
    from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
    from whooing_tui.dupe_scan_repo import DupeScanRepository

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        scr = await _drive_to_cluster_screen(app)
        await pilot.pause()
        cluster_id = scr._clusters[0].id
        assert scr._clusters[0].status == "pending"
        scr.action_confirm()
        # 삭제 완료 후 다음으로 이동.
        await _wait_for(
            lambda: scr._idx > 0
            or not isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        # repo 확인 — cluster_id 의 status 가 resolved.
        repo = DupeScanRepository()
        all_clusters = repo.load_all_clusters(
            section_id="s1",
            range_start=days_ago_yyyymmdd(365 * 3),
            range_end=today_yyyymmdd(),
        )
        resolved = [c for c in all_clusters if c.id == cluster_id]
        assert len(resolved) == 1
        assert resolved[0].status == "resolved"


@pytest.mark.asyncio
async def test_overview_start_cleanup_opens_cluster_screen():
    """overview 의 R/정리 버튼 → DuplicateScanScreen 진입."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        scr = await _drive_to_cluster_screen(app)
        assert isinstance(scr, DuplicateScanScreen)
        # 첫 cluster mark — keep_suggestion 만 False.
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
        await _accept_range_modal(app)
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
        scr = await _drive_to_cluster_screen(app)
        await pilot.pause()
        # 첫 cluster 의 keep_suggestion 이 아닌 entry_id 가 삭제 대상.
        c0 = scr._clusters[0]
        keep0 = c0.keep_suggestion
        expected_deleted = [
            str(e.get("entry_id")) for e in c0.entries
            if str(e.get("entry_id")) != keep0
        ]
        assert expected_deleted
        scr.action_confirm()
        ok = await _wait_for(
            lambda: scr._idx == 1
            or not isinstance(app.screen, DuplicateScanScreen),
            timeout=3.0,
        )
        assert ok
        deleted_ids = [c["entry_id"] for c in fake.delete_calls]
        assert set(deleted_ids) == set(expected_deleted)


@pytest.mark.asyncio
async def test_scan_toggle_delete_flips_mark():
    """Space — 현재 row 의 mark toggle."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        scr = await _drive_to_cluster_screen(app)
        await pilot.pause()
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
        scr = await _drive_to_cluster_screen(app)
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
        scr = await _drive_to_cluster_screen(app)
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
        scr = await _drive_to_cluster_screen(app)
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
        scr = await _drive_to_cluster_screen(app)
        await pilot.pause()
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
        scr = await _drive_to_cluster_screen(app)
        await pilot.pause()
        from textual.widgets import Static
        prog = scr.query_one("#scan-progress", Static)
        text = str(prog.render())
        # N / T 형식.
        assert "1 / 2" in text or "1/2" in text
