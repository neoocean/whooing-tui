"""SectionPickerScreen — Textual App.run_test() 통합.

EntriesScreen 에서 `s` 키로 push, 사용자가 다른 섹션 선택 → dismiss → 자동
재로드 흐름을 검증. dismiss 결과가 (sid, title) 튜플 또는 None.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.sections import SectionPickerScreen


class FakeClient:
    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sections = (
            sections if sections is not None
            else [
                {"section_id": "s1", "title": "main"},
                {"section_id": "s2", "title": "side"},
            ]
        )
        self.accounts_by_section = {
            "s1": {"assets": [{"account_id": "x11", "title": "현금"}]},
            "s2": {"assets": [{"account_id": "x12", "title": "통장"}]},
        }
        self.entries_calls: list[tuple[str, str, str]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts_by_section.get(section_id, {})

    async def list_entries(self, section_id, start, end):
        self.entries_calls.append((section_id, start, end))
        return []

    # ---- 0.84.0 섹션 CRUD/sort (호출 기록) -----------------------------
    async def create_section(self, *, title, currency="KRW", memo=None):
        sid = f"s{len(self.sections) + 1}"
        self.sections.append({"section_id": sid, "title": title})
        self.created = (title, currency, memo)
        return {"section_id": sid}

    async def update_section(self, *, section_id, title=None, **kw):
        for s in self.sections:
            if s["section_id"] == section_id and title is not None:
                s["title"] = title
        self.updated = (section_id, title)
        return {}

    async def delete_section(self, *, section_id):
        self.sections = [s for s in self.sections
                         if s["section_id"] != section_id]
        self.deleted = section_id
        return {}

    async def sort_sections(self, *, section_ids):
        self.sorted_ids = list(section_ids)
        # 서버가 새 순서를 반영하도록 self.sections 재정렬.
        by_id = {s["section_id"]: s for s in self.sections}
        self.sections = [by_id[i] for i in section_ids if i in by_id]
        return list(section_ids)


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.mark.asyncio
async def test_section_picker_pushes_and_lists_sections():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_sections()
        await pilot.pause()
        assert isinstance(app.screen, SectionPickerScreen)
        # 섹션 목록이 채워질 때까지
        from textual.widgets import OptionList
        ok = await _wait_for(
            lambda: app.screen.query_one("#sections-list", OptionList).option_count == 2,
            timeout=2.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_section_picker_dismiss_with_choice_changes_active_section():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_sections()
        await pilot.pause()
        # picker 가 sections 로드 끝낼 때까지
        from textual.widgets import OptionList
        await _wait_for(
            lambda: isinstance(app.screen, SectionPickerScreen)
            and app.screen.query_one("#sections-list", OptionList).option_count == 2,
            timeout=2.0,
        )
        # 사용자가 s2 선택과 같이 dismiss((sid, title)) 직접
        app.screen.dismiss(("s2", "side"))
        # 활성 섹션이 s2 로 바뀌었는지
        ok = await _wait_for(
            lambda: app.session.section_id == "s2"
            and isinstance(app.screen, EntriesScreen),
            timeout=2.0,
        )
        assert ok
        # entries 가 새 섹션으로 재호출됐는지
        assert any(c[0] == "s2" for c in fake.entries_calls), fake.entries_calls


@pytest.mark.asyncio
async def test_section_picker_dismiss_none_keeps_active_section():
    """Esc / 취소 → dismiss(None) → 활성 섹션 그대로."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        before_calls = list(fake.entries_calls)
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_sections()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, SectionPickerScreen), timeout=2.0,
        )
        # 취소
        app.screen.dismiss(None)
        await pilot.pause()
        # 활성 섹션 그대로 + 추가 entries 호출 없음
        assert app.session.section_id == "s1"
        assert fake.entries_calls == before_calls


@pytest.mark.asyncio
async def test_section_picker_same_section_skips_reload():
    """현재 활성 섹션과 동일한 sid 로 dismiss → 의미없는 재로드 안 함."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        before_calls = len(fake.entries_calls)
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_sections()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, SectionPickerScreen), timeout=2.0,
        )
        # 같은 s1 으로 dismiss
        app.screen.dismiss(("s1", "main"))
        await pilot.pause()
        # entries 재호출 안 됨
        assert len(fake.entries_calls) == before_calls


@pytest.mark.asyncio
async def test_section_picker_empty_sections_shows_error():
    fake = FakeClient(sections=[])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # EntriesScreen 의 자체 부팅이 빈 sections 로 실패 → status error.
        # 그 후 사용자가 's' 로 picker 열면, picker 도 같은 빈 응답으로 placeholder.
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=2.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_open_sections()
        await pilot.pause()
        await _wait_for(
            lambda: isinstance(app.screen, SectionPickerScreen)
            and "섹션이 없습니다" in app.screen.last_status,
            timeout=2.0,
        )
        # placeholder option 1개
        from textual.widgets import OptionList
        opt = app.screen.query_one("#sections-list", OptionList)
        assert opt.option_count == 1  # __empty__


# ---- 0.84.0: 섹션 CRUD + 순서 변경 (로드맵 P3-C) ----------------------


async def _open_picker(app, pilot) -> SectionPickerScreen:
    from textual.widgets import OptionList
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1", timeout=3.0,
    )
    app.screen.action_open_sections()
    await pilot.pause()
    await _wait_for(
        lambda: isinstance(app.screen, SectionPickerScreen)
        and app.screen.query_one("#sections-list", OptionList).option_count == 2,
        timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_section_reorder_persists_sort():
    """[ / ] 로 순서 이동 → sort_sections 호출 + 로컬 재정렬."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        # 첫 항목(s1) 하이라이트 상태에서 아래로 이동.
        from textual.widgets import OptionList
        picker.query_one("#sections-list", OptionList).highlighted = 0
        picker.action_move_down()
        await pilot.pause()
        ok = await _wait_for(
            lambda: getattr(fake, "sorted_ids", None) == ["s2", "s1"],
            timeout=2.0,
        )
        assert ok, getattr(fake, "sorted_ids", None)
        # 로컬 순서도 즉시 바뀜.
        assert [s["section_id"] for s in picker._sections] == ["s2", "s1"]


@pytest.mark.asyncio
async def test_section_new_calls_create():
    """n → 제목 입력 → create_section 호출 + 목록에 추가."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)

        async def _fake_wait(_modal):
            return "새 가계부"
        app.push_screen_wait = _fake_wait  # type: ignore[assignment]
        picker.action_new_section()
        ok = await _wait_for(
            lambda: getattr(fake, "created", None) is not None, timeout=2.0,
        )
        assert ok
        assert fake.created[0] == "새 가계부"


@pytest.mark.asyncio
async def test_section_delete_confirmed_calls_delete():
    """d → 확인(True) → delete_section 호출."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)
        from textual.widgets import OptionList
        picker.query_one("#sections-list", OptionList).highlighted = 1  # s2

        async def _fake_wait(_modal):
            return True   # 확인.
        app.push_screen_wait = _fake_wait  # type: ignore[assignment]
        picker.action_delete_section()
        ok = await _wait_for(
            lambda: getattr(fake, "deleted", None) == "s2", timeout=2.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_section_delete_cancelled_skips_delete():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        picker = await _open_picker(app, pilot)

        async def _fake_wait(_modal):
            return False  # 취소.
        app.push_screen_wait = _fake_wait  # type: ignore[assignment]
        picker.action_delete_section()
        await pilot.pause()
        await _wait_for(lambda: "취소" in picker.last_status, timeout=2.0)
        assert getattr(fake, "deleted", None) is None
