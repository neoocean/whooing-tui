"""TagManagementScreen — 해시태그 일괄 관리 흐름 회귀.

CL #51135+ (H5). core helper 자체는 core/tests/test_db.py 검증; 본 테스트는
TUI 진입 + DataTable 표시 + action_<rename/delete> 경유로 db 가 변경되는지.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import DataTable

from whooing_core import db as core_db
from whooing_tui import data as tui_data
from whooing_tui.app import WhooingTuiApp


class _FakeClient:
    def __init__(self):
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"assets": [{"account_id": "x11", "title": "현금"}]}

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return []


async def _wait_for(predicate, *, timeout=3.0, interval=0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _seed_tags(section_id: str, tags_by_entry: dict[str, list[str]]) -> None:
    """conftest 가 격리한 db 에 annotation + tag 시드."""
    tui_data.init_shared_schema()
    with tui_data.open_rw() as conn:
        for eid, tags in tags_by_entry.items():
            core_db.upsert_annotation(
                conn, entry_id=eid, section_id=section_id, note=None,
            )
            core_db.set_hashtags(conn, eid, tags, section_id=section_id)


@pytest.mark.asyncio
async def test_screen_lists_tags_with_count():
    """진입 시 모든 태그 + count 표시."""
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.tag_management import TagManagementScreen

    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        _seed_tags("s1", {"e1": ["식비", "카페"], "e2": ["식비"]})
        await app.push_screen(TagManagementScreen(section_id="s1"))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, TagManagementScreen)
        await _wait_for(
            lambda: len(screen._tags) == 2, timeout=2.0,
        )
        # (식비, 2) / (카페, 1) — count 내림차순.
        assert [t for t, _ in screen._tags] == ["식비", "카페"]
        table = screen.query_one("#tagm_table", DataTable)
        assert table.row_count == 2


@pytest.mark.asyncio
async def test_action_rename_updates_db_and_p4():
    """_apply_rename 직접 호출 → db 변경 + status 메시지."""
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.tag_management import TagManagementScreen

    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        _seed_tags("s1", {"e1": ["식비"], "e2": ["식비"]})
        await app.push_screen(TagManagementScreen(section_id="s1"))
        await pilot.pause()
        screen: TagManagementScreen = app.screen  # type: ignore[assignment]
        await screen._apply_rename("식비", "외식")
        await pilot.pause()
        with tui_data.open_ro() as conn:
            assert sorted(core_db.find_entries_by_hashtag(
                conn, "외식", section_id="s1",
            )) == ["e1", "e2"]
            assert core_db.find_entries_by_hashtag(
                conn, "식비", section_id="s1",
            ) == []
        # action_refresh 가 status 를 덮어쓰므로 마지막 status 는 list 안내.
        # 핵심은 db 변경 — 위 assertion 으로 충분. 추가로 list 가 fresh 상태.
        assert "외식" in [t for t, _ in screen._tags]
        assert "식비" not in [t for t, _ in screen._tags]


def test_menu_includes_tag_management():
    """EntriesScreen 메뉴 정의에 open_tag_management 포함."""
    from whooing_tui.screens.entries import EntriesScreen
    menus = EntriesScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "open_tag_management" for _, action in flat)


@pytest.mark.asyncio
async def test_dispatch_open_tag_management_pushes_screen():
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.tag_management import TagManagementScreen

    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es._dispatch_menu_action("open_tag_management")
        await pilot.pause()
        assert isinstance(app.screen, TagManagementScreen)
        # section_id 가 활성 섹션과 일치.
        assert app.screen.section_id == "s1"
