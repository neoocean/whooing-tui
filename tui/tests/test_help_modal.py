"""HelpModal — 현재 화면의 BINDINGS 를 그대로 표시하는지 검증."""

from __future__ import annotations

import asyncio

import pytest
from textual.binding import Binding

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.help import HelpModal, _format_bindings
from whooing_tui.screens.home import HomeScreen


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
async def test_help_modal_pushes_from_home_and_dismiss_returns():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, HomeScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        # action 직접 호출 — '?' 키 입력 시뮬은 textual 의 modifier 처리가
        # 환경에 따라 차이가 있어 안정성 위해 action 으로.
        app.screen.action_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpModal)
        # 본문에 HomeScreen 의 visible 단축키들이 포함돼 있어야 함
        assert "Entries" in app.screen.body_text
        assert "Refresh" in app.screen.body_text
        assert "Help" in app.screen.body_text
        # 닫기 — 키 시뮬 대신 dismiss 직접 (escape binding 의 textual
        # internal 처리는 환경 의존적이라 단위 테스트에서 단축)
        app.screen.dismiss(None)
        ok = await _wait_for(
            lambda: isinstance(app.screen, HomeScreen), timeout=2.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_help_modal_from_entries_shows_entries_bindings():
    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # Home → Entries → Help
        await _wait_for(
            lambda: isinstance(app.screen, HomeScreen)
            and app.session.id_of("식비") == "x20",
            timeout=3.0,
        )
        await pilot.press("e")
        await _wait_for(lambda: isinstance(app.screen, EntriesScreen), timeout=2.0)
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
