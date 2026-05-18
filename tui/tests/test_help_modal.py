"""HelpModal — 현재 화면의 BINDINGS 를 그대로 표시하는지 검증."""

from __future__ import annotations

import asyncio

import pytest
from textual.binding import Binding

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.help import HelpModal, _format_bindings


# 같은 워크스페이스의 다른 테스트가 사용 중인 FakeClient 와 호환되도록 단순
# 버전을 본 파일에 둔다 (다른 모듈 import 의존을 줄임).
class _FakeClient:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [{"account_id": "x20", "title": "식비"}],
        }
        self.entries: list[dict] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts

    async def list_entries(self, section_id, start, end):
        return list(self.entries)


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


# ---- _format_bindings 단위 ----------------------------------------------


def test_format_bindings_skips_hidden():
    bs = [
        Binding("e", "open_entries", "Entries", show=True),
        Binding("ctrl+l", "refresh", "Refresh", show=False),
        Binding("escape", "focus_sections", "Focus", show=False),
    ]
    out = _format_bindings(bs)
    assert "Entries" in out
    assert "Refresh" not in out
    assert "Focus" not in out


def test_format_bindings_groups_same_description():
    bs = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=True),
    ]
    out = _format_bindings(bs)
    # 같은 description 은 한 줄로 키들이 묶여야 함
    assert out.count("Quit") == 1
    assert "q" in out and "ctrl+c" in out


def test_format_bindings_empty_visible():
    bs = [Binding("x", "secret", "secret", show=False)]
    out = _format_bindings(bs)
    assert "보이는 단축키가 없습니다" in out


# ---- HelpModal 통합 ------------------------------------------------------


@pytest.mark.asyncio
async def test_help_modal_pushes_from_initial_screen_and_dismiss_returns():
    """초기 화면(EntriesScreen)에서 action_help → HelpModal push 후 dismiss 복귀."""
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        # action 직접 호출 — '?' 키 입력 시뮬은 textual 의 modifier 처리가
        # 환경에 따라 차이가 있어 안정성 위해 action 으로.
        app.screen.action_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpModal)
        # 본문에 EntriesScreen 의 visible 단축키들이 포함돼 있어야 함
        assert "Sections" in app.screen.body_text
        assert "Accounts" in app.screen.body_text
        assert "New" in app.screen.body_text
        assert "Refresh" in app.screen.body_text
        assert "Help" in app.screen.body_text
        # 닫기 — 키 시뮬 대신 dismiss 직접
        app.screen.dismiss(None)
        ok = await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=2.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_help_modal_escape_does_not_crash():
    """CL #52816+ regression: HelpModal 안에서 Esc 누르면 textual 의 binding
    chain 이 list (BindingsMap 아님) 를 만나 AttributeError 던지던 버그.

    원인은 HelpModal.__init__ 가 `self._bindings = bindings` 로 Screen 의
    내부 attribute 를 raw list 로 덮어쓴 것. attribute 이름을 `_help_bindings`
    로 바꿔 회피. 본 테스트는 회귀 방지를 위해 실제로 Esc 키를 simulate.
    """
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpModal)
        # 진짜 키 입력 시뮬 — 버그 재현 경로.
        await pilot.press("escape")
        ok = await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=2.0,
        )
        assert ok, "HelpModal 이 Esc 로 닫혀 EntriesScreen 으로 복귀해야"


@pytest.mark.asyncio
async def test_help_modal_from_entries_shows_entries_bindings():
    """동일 화면이지만 entries 가 로드된 후 호출 — visible 키 동일."""
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.id_of("식비") == "x20",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpModal)
        # EntriesScreen 의 키 (New, Delete, Edit, Refresh) 가 들어 있어야
        body = app.screen.body_text
        assert "New" in body
        assert "Delete" in body
        assert "Edit" in body
        assert "Refresh" in body
