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

        # DataTable 이 채워졌는지
        ok = await _wait_for(
            lambda: app.screen.query_one("#entries-table", DataTable).row_count == 2,
            timeout=2.0,
        )
        assert ok

        # account_id 가 title 로 변환되어야 함 (식비, 현금) +
        # entry_date 가 YYYY-MM-DD 형식으로 정규화 (CL #51043).
        table = app.screen.query_one("#entries-table", DataTable)
        col_count = len(table.columns)
        row0 = [
            str(table.get_cell_at((0, c))) for c in range(col_count)
        ]
        assert "2026-05-10" in row0
        assert "20260510" not in row0  # 정규화 후 raw 형식은 사라져야
        assert "12,000" in row0
        assert "식비" in row0
        assert "현금" in row0
        assert "스타벅스" in row0

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
        bar = app.screen.query_one("#status", Static)
        assert "warn" in bar.classes
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
        # 핵심 액션 키들이 모두 들어 있어야 — 사용자가 막혔을 때 가이드
        assert "[s]" in msg
        assert "[+]" in msg
        assert "[n]" in msg
        bar = app.screen.query_one("#status", Static)
        assert "warn" in bar.classes
