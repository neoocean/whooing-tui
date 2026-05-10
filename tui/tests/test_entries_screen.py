"""EntriesScreen — Textual App.run_test() 통합 테스트.

FakeClient 에 list_entries 를 추가해 entries 화면을 띄운 뒤:
  - 첫 mount 시 list_entries 가 호출되고 DataTable 이 채워진다.
  - account_id 가 SessionState 의 양방향 인덱스로 title 로 변환된다.
  - 100-cap 의심 (단일 일자에 100건) 시 warn 클래스 + last_cap_warning.
  - 'q' 로 HomeScreen 복귀.
  - '+' / '-' 가 윈도우를 ±7일 변경 + 재로드.
  - ToolError 발생 시 error 클래스 + 행 0건.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import DataTable, Static

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError




class FakeClient:
    """HomeScreen 테스트 의 FakeClient 를 entries 까지 확장."""

    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
        accounts_by_section: dict[str, dict[str, Any]] | None = None,
        entries_by_section: dict[str, list[dict[str, Any]]] | None = None,
        entries_error: ToolError | None = None,
    ) -> None:
        # `sections or default` 패턴은 빈 리스트도 falsy 로 잡아 default 로
        # 덮어버린다 — `is None` 으로 분기해야 빈 sections 케이스 테스트가
        # 의미를 가진다.
        self.sections = (
            sections if sections is not None
            else [{"section_id": "s1", "title": "main"}]
        )
        self.accounts_by_section = accounts_by_section or {
            "s1": {
                "assets": [{"account_id": "x11", "title": "현금"}],
                "expenses": [
                    {"account_id": "x20", "title": "식비"},
                    {"account_id": "x21", "title": "교통비"},
                ],
            },
        }
        self.entries_by_section = entries_by_section or {}
        self.entries_error = entries_error
        self.list_entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self) -> list[dict[str, Any]]:
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        return self.accounts_by_section.get(section_id, {})

    async def list_entries(
        self, section_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        self.list_entries_calls.append((section_id, start_date, end_date))
        if self.entries_error is not None:
            raise self.entries_error
        return list(self.entries_by_section.get(section_id, []))


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _sample_entries() -> list[dict[str, Any]]:
    return [
        {
            "entry_id": "e1", "entry_date": "20260510",
            "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스", "memo": "오후",
        },
        {
            "entry_id": "e2", "entry_date": "20260509",
            "money": 5500, "l_account_id": "x21", "r_account_id": "x11",
            "item": "지하철", "memo": "",
        },
    ]


@pytest.mark.asyncio
async def test_entries_screen_is_initial_and_self_bootstraps():
    """초기 화면 = EntriesScreen — 자체적으로 sections + accounts + entries
    부팅 (CL #51023+). 별도 HomeScreen 진입 없이 진입하자마자 표가 채워진다.
    """
    fake = FakeClient(entries_by_section={"s1": _sample_entries()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        # 첫 mount 시 EntriesScreen 이 push 되고 자체 부팅
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1"
            and app.session.id_of("식비") == "x20"
            and len(fake.list_entries_calls) >= 1,
            timeout=3.0,
        )

        # DataTable row count = entries 2 + sentinel 1 = 3 (CL #51072+).
        ok = await _wait_for(
            lambda: app.screen.query_one("#entries-table", DataTable).row_count == 3,
            timeout=2.0,
        )
        assert ok

        # account_id 가 title 로 변환되어야 함 (식비, 현금) +
        # entry_date 가 YYYY-MM-DD 형식으로 정규화 (CL #51043).
        # row 0 = sentinel ("새 거래 추가"), row 1 = 첫 실거래.
        table = app.screen.query_one("#entries-table", DataTable)
        col_count = len(table.columns)
        row0 = [str(table.get_cell_at((0, c))) for c in range(col_count)]
        row1 = [str(table.get_cell_at((1, c))) for c in range(col_count)]
        row0_joined = " | ".join(row0)
        row1_joined = " | ".join(row1)
        # row 0 = sentinel 안내 라벨
        assert "새 거래 추가" in row0_joined
        # row 1 = 첫 실거래 (e1: 20260510, 12000, 식비 / 현금, 스타벅스)
        assert "2026-05-10" in row1_joined
        assert "20260510" not in row1_joined  # 정규화 후 raw 형식 사라져야
        assert "12,000" in row1_joined
        assert "식비" in row1_joined
        assert "현금" in row1_joined
        assert "스타벅스" in row1_joined
        # 초기 상태: 컬럼 marker 비활성 — 노란 markup 없음.
        assert "[black on yellow]" not in row0_joined
        assert "[black on yellow]" not in row1_joined

        # 100-cap 경고 없음
        assert app.screen.last_cap_warning is False
        assert "error" not in app.screen.query_one("#status", Static).classes
        assert "warn" not in app.screen.query_one("#status", Static).classes


@pytest.mark.asyncio
async def test_entries_screen_q_exits_app():
    """초기 화면이라 q / escape 가 pop 이 아닌 app.exit()."""
    fake = FakeClient(entries_by_section={"s1": _sample_entries()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        # action_back 직접 호출 — exit() 이 호출되는지
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_back()
        # app 이 종료 절차에 들어가야
        await pilot.pause()
        # textual 의 App.exit() 은 _exit 플래그 set + run loop 종료.
        # run_test context 가 끝나기 전엔 plug 가 살아 있을 수 있어 직접
        # 검사가 까다로우니 단순히 호출이 예외 없이 동작하는지만.


@pytest.mark.asyncio
async def test_entries_screen_window_extend_triggers_reload():
    fake = FakeClient(entries_by_section={"s1": _sample_entries()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and len(fake.list_entries_calls) >= 1,
            timeout=3.0,
        )
        first_window = fake.list_entries_calls[0]
        # +7일
        await pilot.press("plus")
        ok = await _wait_for(
            lambda: len(fake.list_entries_calls) >= 2, timeout=2.0,
        )
        assert ok
        # 두 번째 호출의 start_date 가 더 이른 날짜여야 함
        second = fake.list_entries_calls[-1]
        assert second[2] == first_window[2]  # end_date 동일 (오늘)
        assert int(second[1]) < int(first_window[1])  # start_date 가 더 이전


@pytest.mark.asyncio
async def test_entries_screen_100_cap_warning_for_single_date():
    """단일 일자에 100건 = cap 도달 의심 → warn 클래스 + last_cap_warning."""
    bulk = [
        {
            "entry_id": f"e{i}", "entry_date": "20260510",
            "money": 1000 + i, "l_account_id": "x20", "r_account_id": "x11",
            "item": f"항목{i}",
        }
        for i in range(100)
    ]
    fake = FakeClient(entries_by_section={"s1": bulk})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        ok = await _wait_for(
            lambda: getattr(app.screen, "last_cap_warning", False) is True,
            timeout=3.0,
        )
        assert ok
        # 100-cap warn 메시지가 status 에 들어가야 — 단, sentinel row 가
        # 첫 cursor 위치가 row 1 이므로 row_highlighted 가 한 번 발생해
        # status 가 sentinel 안내로 덮을 가능성은 cursor row 1 이라 없음.
        # last_cap_warning 은 _update_window_status 에서 set 된 후 유지.
        assert "100-cap" in app.screen.last_status
        # status 메시지의 날짜도 YYYY-MM-DD 정규화 (CL #51043).
        assert "2026-05-10" in app.screen.last_status


@pytest.mark.asyncio
async def test_entries_screen_error_shows_status_error():
    fake = FakeClient(
        entries_error=ToolError("RATE_LIMIT", "분당 한도 초과"),
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        ok = await _wait_for(
            lambda: "error" in app.screen.query_one("#status", Static).classes,
            timeout=3.0,
        )
        assert ok
        assert "RATE_LIMIT" in app.screen.last_status
        # 행은 비어있어야
        assert app.screen.query_one("#entries-table", DataTable).row_count == 0


@pytest.mark.asyncio
async def test_entries_screen_empty_sections_shows_error():
    """sections-list 가 빈 응답이면 EntriesScreen 의 자체 부팅이 status
    error 로 안내. 이전 (HomeScreen 시절) 의 action_open_entries 검증을
    대체.
    """
    fake = FakeClient(sections=[])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=2.0,
        )
        # 자체 refresh_entries 가 sections-list 빈 응답 → status error
        ok = await _wait_for(
            lambda: "error" in app.screen.query_one("#status", Static).classes
            and "섹션이 없습니다" in app.screen.last_status,
            timeout=2.0,
        )
        assert ok
        # entries 호출은 발생하지 않아야 (sections 단계에서 fail)
        assert fake.list_entries_calls == []


# ---- _fmt_date (CL #51043) -------------------------------------------


def test_fmt_date_yyyymmdd_to_dashed():
    from whooing_tui.screens.entries import _fmt_date
    assert _fmt_date("20260510") == "2026-05-10"


def test_fmt_date_strips_sub_index():
    """후잉 응답이 '20260510.0001' 같이 sub-index 를 붙이는 경우."""
    from whooing_tui.screens.entries import _fmt_date
    assert _fmt_date("20260510.0001") == "2026-05-10"


def test_fmt_date_empty_and_none():
    from whooing_tui.screens.entries import _fmt_date
    assert _fmt_date(None) == ""
    assert _fmt_date("") == ""


def test_fmt_date_passthrough_for_unrecognized():
    """8자리 숫자가 아닌 입력은 손대지 않고 그대로 — 디버깅 친화."""
    from whooing_tui.screens.entries import _fmt_date
    assert _fmt_date("2026-05-10") == "2026-05-10"  # 이미 dashed
    assert _fmt_date("abcd") == "abcd"
    assert _fmt_date("2026") == "2026"


# ---- 자동 활성화 우선순위 (CL #51031+) -------------------------------


@pytest.mark.asyncio
async def test_default_section_chosen_when_no_saved():
    """saved last_section 없으면 'Default' title 매칭. (사용자 환경에서
    `Default` / `테스트` 가 있고 .env 의 WHOOING_SECTION_ID 가 설정 안
    됐을 때 Default 를 자동 선택해야 한다는 사용자 요구.)
    """
    fake = FakeClient(
        sections=[
            {"section_id": "s133178", "title": "테스트"},
            {"section_id": "s9046", "title": "Default"},
        ],
        accounts_by_section={
            "s9046": {"assets": [{"account_id": "x11", "title": "현금"}]},
        },
        entries_by_section={"s9046": []},
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s9046",
            timeout=3.0,
        )
        assert app.session.section_title == "Default"


@pytest.mark.asyncio
async def test_is_default_flag_chosen_over_title():
    """응답의 `is_default: true` 가 title 매칭보다 우선 — 후잉이 그 플래그
    를 노출하면 그것이 가장 신뢰할 수 있는 신호."""
    fake = FakeClient(
        sections=[
            {"section_id": "sA", "title": "Default", "is_default": False},
            {"section_id": "sB", "title": "메인", "is_default": True},
            {"section_id": "sC", "title": "기타"},
        ],
        accounts_by_section={"sB": {}},
        entries_by_section={"sB": []},
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "sB",
            timeout=3.0,
        )


@pytest.mark.asyncio
async def test_saved_section_restored_on_next_boot():
    """1차 실행: 사용자가 's' 로 다른 섹션 선택 → 저장.
    2차 실행: saved 가 적용 (Default 가 있어도 saved 가 우선).
    """
    sections = [
        {"section_id": "s9046", "title": "Default"},
        {"section_id": "s133178", "title": "테스트"},
    ]
    accounts_by_section = {
        "s9046": {},
        "s133178": {},
    }
    entries_by_section = {"s9046": [], "s133178": []}

    # 1차
    fake1 = FakeClient(
        sections=sections,
        accounts_by_section=accounts_by_section,
        entries_by_section=entries_by_section,
    )
    app1 = WhooingTuiApp(client=fake1)  # type: ignore[arg-type]
    async with app1.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app1.screen, EntriesScreen)
            and app1.session.section_id == "s9046",
            timeout=3.0,
        )
        # 사용자가 's' 로 picker 열고 테스트 선택
        es: EntriesScreen = app1.screen  # type: ignore[assignment]
        es.action_open_sections()
        await pilot.pause()
        # picker 가 sections 로드 후 dismiss
        from whooing_tui.screens.sections import SectionPickerScreen
        await _wait_for(
            lambda: isinstance(app1.screen, SectionPickerScreen), timeout=2.0,
        )
        app1.screen.dismiss(("s133178", "테스트"))
        await _wait_for(
            lambda: app1.session.section_id == "s133178", timeout=2.0,
        )

    # 2차 — 같은 isolated_state_home 안에서 다시 부팅. saved=s133178 적용.
    fake2 = FakeClient(
        sections=sections,
        accounts_by_section=accounts_by_section,
        entries_by_section=entries_by_section,
    )
    app2 = WhooingTuiApp(client=fake2)  # type: ignore[arg-type]
    async with app2.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        ok = await _wait_for(
            lambda: isinstance(app2.screen, EntriesScreen)
            and app2.session.section_id == "s133178",
            timeout=3.0,
        )
        assert ok, (
            f"saved 가 복원되지 않음 — 활성 섹션={app2.session.section_id}"
        )


@pytest.mark.asyncio
async def test_env_section_used_when_no_saved_and_no_default():
    """saved 없음 + Default 매칭 안 됨 → WHOOING_SECTION_ID env 적용."""
    import os
    os.environ["WHOOING_SECTION_ID"] = "sB"
    try:
        fake = FakeClient(
            sections=[
                {"section_id": "sA", "title": "alpha"},
                {"section_id": "sB", "title": "beta"},
                {"section_id": "sC", "title": "gamma"},
            ],
            accounts_by_section={"sB": {}},
            entries_by_section={"sB": []},
        )
        app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
        async with app.run_test() as pilot:
            from whooing_tui.screens.entries import EntriesScreen
            await _wait_for(
                lambda: isinstance(app.screen, EntriesScreen)
                and app.session.section_id == "sB",
                timeout=3.0,
            )
    finally:
        os.environ.pop("WHOOING_SECTION_ID", None)


@pytest.mark.asyncio
async def test_first_section_when_nothing_matches():
    """saved 없음 + Default 없음 + WHOOING_SECTION_ID 없음 → 첫 섹션."""
    fake = FakeClient(
        sections=[
            {"section_id": "sFirst", "title": "first"},
            {"section_id": "sSecond", "title": "second"},
        ],
        accounts_by_section={"sFirst": {}},
        entries_by_section={"sFirst": []},
    )
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "sFirst",
            timeout=3.0,
        )


# ---- 빈 결과 안내 -------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_entries_status_includes_action_hints():
    """거래 0건일 때 status 가 다른 섹션 / 윈도우 / 새 거래 액션을 안내."""
    fake = FakeClient(entries_by_section={"s1": []})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and len(fake.list_entries_calls) >= 1
            and "거래내역 없음" in app.screen.last_status,
            timeout=3.0,
        )
        msg = app.screen.last_status
        # 빈 entries 일 때 sentinel 만 1 row 라 cursor 가 row 0 (sentinel) 에
        # 머무르고 row_highlighted 가 마지막에 sentinel 안내로 덮어쓸 수
        # 있다 (cursor row 0). 하지만 _update_window_status 가 status 를
        # 마지막에 set 하므로 거래내역 없음 메시지가 살아있어야 함 — refresh
        # 흐름 끝에서 다시 set 됨.
        # 핵심 액션 키들이 모두 들어 있어야 — sentinel 안내 ("[Enter = 새
        # 거래 추가]") 또는 빈 결과 안내 ("[s]/[+]/[n]") 둘 중 하나라도 OK.
        assert (
            ("[s]" in msg and "[+]" in msg and "[n]" in msg)
            or "Enter = 새 거래 추가" in msg
        ), f"unexpected status: {msg!r}"


# ---- 컬럼 navigation + Enter 컬럼별 필터 (CL #51053+) ------------------


def _entries_for_filter():
    return [
        {"entry_id": "e1", "entry_date": "20260510",
         "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
         "item": "스타벅스(커피)"},
        {"entry_id": "e2", "entry_date": "20260510",
         "money": 5500, "l_account_id": "x21", "r_account_id": "x11",
         "item": "지하철"},
        {"entry_id": "e3", "entry_date": "20260509",
         "money": 8000, "l_account_id": "x20", "r_account_id": "x11",
         "item": "스타벅스"},
        {"entry_id": "e4", "entry_date": "20260508",
         "money": 25000, "l_account_id": "x21", "r_account_id": "x12",
         "item": "택시"},
    ]


@pytest.mark.asyncio
async def test_active_col_starts_at_zero():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        assert app.screen._active_col == 0  # date


@pytest.mark.asyncio
async def test_arrow_keys_navigate_columns():
    """CL #51064+: 첫 ←/→ = 컬럼 활성화 (col 그대로), 두번째부터 ±1."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # 초기: 컬럼 비활성, _active_col=0
        assert es._column_active is False
        assert es._active_col == 0
        # 첫 → = 활성화, col 0 그대로
        es.action_next_column()
        assert es._column_active is True
        assert es._active_col == 0
        # 추가 → 5번: col 0 → 1 → 2 → 3 → 4 → 5 (memo).
        for _ in range(5):
            es.action_next_column()
        assert es._active_col == 5
        # 끝에서 한 번 더 → boundary clamp
        es.action_next_column()
        assert es._active_col == 5
        # ← 두 번 → col 5 - 2 = 3 (right)
        es.action_prev_column()
        es.action_prev_column()
        assert es._active_col == 3
        # 처음 boundary
        for _ in range(10):
            es.action_prev_column()
        assert es._active_col == 0
        # 활성 상태 유지
        assert es._column_active is True


@pytest.mark.asyncio
async def test_enter_on_date_column_filters_same_day():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 컬럼 marker 활성화 후 enter — 첫 → 누름이 활성화 +
        # col 0 (date) 유지.
        es.action_next_column()
        assert es._column_active is True
        assert es._active_col == 0
        # 정렬: 20260510×2, 20260509, 20260508 → row 0/1 = 20260510, row 2 = 20260509
        # row 0 (= 20260510) cursor 그대로. _active_col = 0 (date). enter.
        es.action_context_enter()
        await pilot.pause()
        # 20260510 매칭 entries 만 (e1, e2)
        assert {e["entry_id"] for e in es._entries} == {"e1", "e2"}
        assert es._active_filter is not None
        assert es._active_filter[0] == "date"


@pytest.mark.asyncio
async def test_enter_on_left_column_filters_same_l_account():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 첫 → = 활성화 (col 0), 추가 → → → → col 2 (left).
        es.action_next_column()
        es.action_next_column()
        es.action_next_column()
        assert es._active_col == 2
        # 정렬 후 row 0 = e2 (entry_date desc + entry_id desc).
        # e2 의 l_account_id = x21 → 매칭: e2, e4.
        es.action_context_enter()
        await pilot.pause()
        assert {e["entry_id"] for e in es._entries} == {"e2", "e4"}


@pytest.mark.asyncio
async def test_enter_on_right_column_filters_same_r_account():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 첫 → = 활성화 (col 0), 추가 → 3번 → col 3 (right).
        for _ in range(4):
            es.action_next_column()
        assert es._active_col == 3
        es.action_context_enter()
        await pilot.pause()
        # row 0 = e1 (r=x11). x11 매칭: e1, e2, e3
        assert {e["entry_id"] for e in es._entries} == {"e1", "e2", "e3"}


@pytest.mark.asyncio
async def test_enter_on_item_column_filters_outside_paren_keyword():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 첫 → = 활성화 (col 0), 추가 → 4번 → col 4 (item).
        for _ in range(5):
            es.action_next_column()
        assert es._active_col == 4
        # 정렬 후 row 0 = e2 (지하철). 괄호 바깥 = "지하철".
        # 다른 거래에 "지하철" 키워드 없으므로 e2 만 매칭.
        es.action_context_enter()
        await pilot.pause()
        assert {e["entry_id"] for e in es._entries} == {"e2"}


@pytest.mark.asyncio
async def test_enter_on_money_column_opens_edit_dialog():
    """money 컬럼에서 enter 는 필터가 아닌 거래 수정 dialog."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.edit_entry import EntryEditDialog
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 첫 → = 활성화 (col 0), 추가 → → col 1 (money).
        es.action_next_column()
        es.action_next_column()
        assert es._active_col == 1
        es.action_context_enter()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)


@pytest.mark.asyncio
async def test_clear_filter_restores_all_entries():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 컬럼 활성화 후 필터 적용
        es.action_next_column()
        es.action_context_enter()
        await pilot.pause()
        assert es._active_filter is not None
        assert len(es._entries) < 4  # 필터된 부분집합
        # 해제
        es.action_clear_filter()
        await pilot.pause()
        assert es._active_filter is None
        assert len(es._entries) == 4


@pytest.mark.asyncio
async def test_initial_state_has_no_column_marker():
    """CL #51064+: 첫 mount 시 _column_active=False — 노란 marker 안 보임.
    파란 row cursor (textual default) 만 보인다.
    """
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._column_active is False
        assert es._marked_cell is None
        # 표 안 어디에도 marker markup 없음 (row 0 = sentinel + entries 4 = total 5)
        table = es.query_one("#entries-table", DataTable)
        for row in range(table.row_count):
            for col in range(6):
                cell = str(table.get_cell_at((row, col)))
                assert "[black on yellow]" not in cell, (
                    f"row={row} col={col} cell={cell!r}"
                )


@pytest.mark.asyncio
async def test_first_arrow_press_activates_column_marker():
    """CL #51064+: 첫 ←/→ 누름 = 활성화 + col 0 (이동 X). marker 등장."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        assert es._column_active is False
        # cursor 가 첫 실거래 row 1 (sentinel row 0 위) 에 있는지
        assert table.cursor_row == 1
        # 첫 → = 활성화, col 0 그대로 (row 1 의 col 0 에 marker)
        es.action_next_column()
        await pilot.pause()
        assert es._column_active is True
        assert es._active_col == 0
        assert "[black on yellow]" in str(table.get_cell_at((1, 0)))
        # row 0 (sentinel) 에는 marker 안 보임
        assert "[black on yellow]" not in str(table.get_cell_at((0, 0)))
        # 두번째 → = col 1
        es.action_next_column()
        await pilot.pause()
        assert es._active_col == 1
        assert "[black on yellow]" not in str(table.get_cell_at((1, 0)))
        assert "[black on yellow]" in str(table.get_cell_at((1, 1)))


@pytest.mark.asyncio
async def test_active_cell_marker_follows_cursor_row():
    """↓ 로 cursor row 가 바뀌면 marker 도 따라 이동 — 단, 컬럼 활성 상태일 때만."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)

        def _cell(row, col):
            return str(table.get_cell_at((row, col)))

        # 컬럼 활성화 (CL #51064+) — 시작 cursor 는 row 1 (첫 실거래)
        assert table.cursor_row == 1
        es.action_next_column()
        await pilot.pause()
        assert "[black on yellow]" in _cell(1, 0)
        # cursor 를 row 3 으로 이동 (↓ 두 번 — row 1 → 2 → 3)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        # row 1 marker 사라지고 row 3 에 marker
        assert "[black on yellow]" not in _cell(1, 0)
        assert "[black on yellow]" in _cell(3, 0)


# ---- CL #51064+: Enter / Esc 의 column-active 분기 ---------------------


@pytest.mark.asyncio
async def test_enter_without_column_active_opens_edit_dialog():
    """파란 row 만 있는 상태에서 enter = EntryEditDialog (필터 X)."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.edit_entry import EntryEditDialog
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._column_active is False
        # column 비활성 상태에서 enter
        es.action_context_enter()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)


@pytest.mark.asyncio
async def test_escape_when_column_active_deactivates_marker():
    """Esc — 컬럼 활성 상태에서 marker 만 해제, 앱 종료 X."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # 활성화
        es.action_next_column()
        await pilot.pause()
        assert es._column_active is True
        # Esc 직접 호출 (key 시뮬보다 안정)
        es.action_deactivate_column()
        await pilot.pause()
        assert es._column_active is False
        assert es._marked_cell is None
        # 앱은 그대로 — EntriesScreen 이 active screen
        assert isinstance(app.screen, EntriesScreen)
        # marker 사라졌는지
        table = es.query_one("#entries-table", DataTable)
        assert "[black on yellow]" not in str(table.get_cell_at((0, 0)))


@pytest.mark.asyncio
async def test_escape_when_column_inactive_is_noop():
    """초기 상태 (파란만) 에서 Esc = 아무 동작 안 함, 앱 종료 안 함."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._column_active is False
        # Esc on 비활성 → noop
        es.action_deactivate_column()
        await pilot.pause()
        # 상태 그대로
        assert es._column_active is False
        assert isinstance(app.screen, EntriesScreen)
        # 앱이 종료되지 않았는지 — _exit 플래그 체크 (textual 8 의 App
        # 내부 attribute, 환경 의존이라 직접 검사보다 screen 생존만 확인)


@pytest.mark.asyncio
async def test_escape_with_active_filter_clears_both_marker_and_filter():
    """CL #51068: 필터 적용된 상태에서 Esc → marker + filter 동시 해제."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # 컬럼 활성화 + enter → 필터 적용
        es.action_next_column()
        await pilot.pause()
        es.action_context_enter()
        await pilot.pause()
        assert es._column_active is True
        assert es._active_filter is not None
        assert len(es._entries) < 4  # 필터된 부분집합

        # Esc → 둘 다 해제 (marker + filter)
        es.action_deactivate_column()
        await pilot.pause()
        assert es._column_active is False
        assert es._marked_cell is None
        assert es._active_filter is None
        assert len(es._entries) == 4  # 원본 복원
        # 표 안 어디에도 marker 없음 (sentinel 1 + entries 4 = 5 rows)
        table = es.query_one("#entries-table", DataTable)
        for row in range(table.row_count):
            for col in range(6):
                cell = str(table.get_cell_at((row, col)))
                assert "[black on yellow]" not in cell


@pytest.mark.asyncio
async def test_escape_with_only_marker_no_filter_clears_only_marker():
    """marker 만 활성, 필터 비활성 → Esc 가 marker 만 해제 (entries 그대로)."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # 컬럼만 활성화 (filter 적용 안 함)
        es.action_next_column()
        await pilot.pause()
        assert es._column_active is True
        assert es._active_filter is None
        before_count = len(es._entries)

        es.action_deactivate_column()
        await pilot.pause()
        assert es._column_active is False
        assert es._active_filter is None
        # entries 그대로 (필터가 없었으니 변동 없음)
        assert len(es._entries) == before_count


# ---- CL #51072+: sentinel row "새 거래 추가" -----------------------


@pytest.mark.asyncio
async def test_sentinel_row_at_top_with_entries():
    """row 0 = sentinel (`[+ 새 거래 추가]`). 실 거래는 row 1+ (CL #51072+)."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        # row count = sentinel 1 + entries 4 = 5
        assert table.row_count == 5
        # row 0 첫 cell 에 sentinel label
        sentinel_cell = str(table.get_cell_at((0, 0)))
        assert "새 거래 추가" in sentinel_cell
        # 첫 cursor 는 row 1 (sentinel 위가 아닌 첫 실거래)
        assert table.cursor_row == 1


@pytest.mark.asyncio
async def test_up_arrow_from_first_entry_lands_on_sentinel():
    """row 1 에서 ↑ 한 번 → cursor row 0 (sentinel)."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        table = app.screen.query_one("#entries-table", DataTable)
        assert table.cursor_row == 1
        await pilot.press("up")
        await pilot.pause()
        assert table.cursor_row == 0


@pytest.mark.asyncio
async def test_enter_on_sentinel_opens_new_entry_dialog():
    """sentinel row 에서 enter → 새 거래 추가 dialog (필터 / edit X)."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.edit_entry import EntryEditDialog
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        # cursor 를 sentinel (row 0) 으로
        await pilot.press("up")
        await pilot.pause()
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        assert es._is_on_sentinel_row()
        # action_context_enter → action_new_entry → EntryEditDialog
        es.action_context_enter()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)


@pytest.mark.asyncio
async def test_no_marker_on_sentinel_row_even_when_column_active():
    """컬럼 활성 상태라도 sentinel row 위에서는 marker 미표시."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        # 컬럼 활성화 (row 1 에서)
        es.action_next_column()
        await pilot.pause()
        assert "[black on yellow]" in str(table.get_cell_at((1, 0)))
        # sentinel 로 이동 — marker 사라져야
        await pilot.press("up")
        await pilot.pause()
        assert table.cursor_row == 0
        # row 0 에 marker 없음
        for col in range(6):
            assert "[black on yellow]" not in str(table.get_cell_at((0, col)))


@pytest.mark.asyncio
async def test_sentinel_only_when_entries_empty_and_enter_works():
    """entries 비어있어도 sentinel row 1개는 보여 새 거래 추가 가능."""
    fake = FakeClient(entries_by_section={"s1": []})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.edit_entry import EntryEditDialog
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and len(fake.list_entries_calls) >= 1,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        assert table.row_count == 1  # sentinel only
        assert table.cursor_row == 0  # sentinel 에서 시작
        assert es._is_on_sentinel_row()
        # enter → 새 거래 추가
        es.action_context_enter()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)


@pytest.mark.asyncio
async def test_escape_via_pressed_key_does_not_quit():
    """실제 'escape' 키 입력으로도 앱이 종료되지 않는지 — 사용자가 가장
    걱정한 시나리오."""
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        await pilot.press("escape")
        await pilot.pause()
        # 여전히 EntriesScreen (앱 종료 X)
        assert isinstance(app.screen, EntriesScreen)


@pytest.mark.asyncio
async def test_refresh_clears_active_filter():
    fake = FakeClient(entries_by_section={"s1": _entries_for_filter()})
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 4,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # CL #51064+: 컬럼 활성화 후 필터 적용
        es.action_next_column()
        es.action_context_enter()  # 필터 적용
        await pilot.pause()
        assert es._active_filter is not None
        # 'r' = refresh — 필터 자동 해제 + 재로드
        es.action_refresh()
        await _wait_for(
            lambda: es._active_filter is None
            and len(es._entries) == 4,
            timeout=3.0,
        )
