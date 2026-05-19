"""AccountPickerScreen — Textual App.run_test() 통합.

EntryEditDialog 의 left/right 버튼 → AccountPickerScreen 으로 push,
사용자가 항목 선택 → dismiss((account_id, title, type_key)) 흐름을 검증.

CL #51076+: free-text account 입력은 사라지고 본 picker 가 유일한 진입점.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.account_picker import AccountPickerScreen
from whooing_tui.screens.edit_entry import EntryEditDialog, _AccountButton
from whooing_tui.screens.entries import EntriesScreen


class FakeClient:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x21", "title": "교통비"},
            ],
        }
        self._entries: list[dict[str, Any]] = [
            {
                "entry_id": "e1", "entry_date": "20260510",
                "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
                "item": "스타벅스",
            },
        ]

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def create_entry(self, **kwargs):
        return {"entry_id": "new-1", **kwargs}

    async def update_entry(self, **kwargs):
        return {**kwargs}

    async def delete_entry(self, **kwargs):
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
        and app.session.section_id == "s1"
        and app.session.id_of("식비") == "x20",
        timeout=3.0,
    )
    await _wait_for(
        lambda: app.screen.last_entry_count >= 1, timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_picker_pushes_from_edit_dialog_and_updates_button():
    """edit dialog 의 left 버튼 클릭 → picker push → 항목 선택 →
    버튼 라벨이 새 항목으로 갱신."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)  # noqa: F841
        es.action_edit_entry()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)
        dialog = app.screen
        # left 버튼은 현재 식비 (x20)
        left_btn = dialog.query_one("#f-left", _AccountButton)
        assert left_btn.account_id == "x20"
        # 버튼 클릭 트리거 — picker push
        dialog._open_account_picker("f-left")
        await pilot.pause()
        assert isinstance(app.screen, AccountPickerScreen)
        # 사용자가 교통비 (x21) 선택한 것과 동일하게 dismiss
        app.screen.dismiss(("x21", "교통비", "expenses"))
        await pilot.pause()
        # dialog 가 다시 활성, 버튼 라벨 갱신됨
        assert isinstance(app.screen, EntryEditDialog)
        assert left_btn.account_id == "x21"
        assert left_btn.acc_title == "교통비"
        assert left_btn.type_key == "expenses"


@pytest.mark.asyncio
async def test_picker_cancel_keeps_button():
    """picker 에서 Esc — dismiss(None) — 버튼 라벨 유지."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app)  # noqa: F841
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        right_btn = dialog.query_one("#f-right", _AccountButton)
        before = (right_btn.account_id, right_btn.acc_title)
        dialog._open_account_picker("f-right")
        await pilot.pause()
        assert isinstance(app.screen, AccountPickerScreen)
        app.screen.dismiss(None)
        await pilot.pause()
        # 버튼 그대로
        assert (right_btn.account_id, right_btn.acc_title) == before


@pytest.mark.asyncio
async def test_picker_lists_categories_with_items_as_leaves():
    """Tree 가 카테고리(branch) → 항목(leaf) 2-level. 자산→지출 순 + 항목
    leaf 의 data = (account_id, title, type_key)."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import Tree

        await app.push_screen(AccountPickerScreen(app.session, side="left"))
        await pilot.pause()
        tree = app.screen.query_one("#acc-tree", Tree)
        cats = list(tree.root.children)
        # 카테고리 데이터 type_key 순 — 자산 (assets) 먼저, expenses 다음.
        assert [c.data for c in cats] == ["assets", "expenses"]
        # leaf 의 data 가 (id, title, type_key) 튜플
        assets_leaves = [g.data for g in cats[0].children]
        assert assets_leaves == [("x11", "현금", "assets")]
        expense_leaves = [g.data for g in cats[1].children]
        assert expense_leaves == [
            ("x20", "식비", "expenses"),
            ("x21", "교통비", "expenses"),
        ]


@pytest.mark.asyncio
async def test_picker_auto_expands_current_id_category():
    """`current_id` 가 속한 카테고리만 자동 펼침 + cursor 가 그 leaf 위."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import Tree

        await app.push_screen(
            AccountPickerScreen(app.session, side="left", current_id="x20"),
        )
        await pilot.pause()
        tree = app.screen.query_one("#acc-tree", Tree)
        cats = list(tree.root.children)
        # 자산은 접혀있어야, 지출은 펼침
        assets_cat = next(c for c in cats if c.data == "assets")
        expenses_cat = next(c for c in cats if c.data == "expenses")
        assert assets_cat.is_expanded is False
        assert expenses_cat.is_expanded is True


@pytest.mark.asyncio
async def test_picker_branch_enter_toggles_expand():
    """카테고리 위에서 Enter (action_select_cursor) → 펼침/접힘 토글, 모달은 유지.

    CL #51087 회귀 방지: 이전에는 본 핸들러가 명시적으로 토글했는데
    Tree 의 `auto_expand` 와 두 번 토글되어 사용자 Enter 가 무효처럼
    보였다. 본 테스트는 *실제 키 흐름* (action_select_cursor) 를 통해
    검증 — 사용자 환경에서 한 번만 토글되는지.
    """
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import Tree

        await app.push_screen(AccountPickerScreen(app.session, side="left"))
        await pilot.pause()
        tree = app.screen.query_one("#acc-tree", Tree)
        cats = list(tree.root.children)
        target = next(c for c in cats if c.data == "expenses")
        # 초기 expansion 기록
        was_expanded = target.is_expanded
        # cursor 를 target 카테고리로 옮기고 select 호출 — 사용자 Enter
        # 와 동일한 흐름 (auto_expand + on_tree_node_selected 양쪽 통과).
        tree.move_cursor(target)
        tree.action_select_cursor()
        await pilot.pause()
        assert target.is_expanded is (not was_expanded)
        # 모달 그대로 — branch 토글 후 dismiss 되지 않음.
        assert isinstance(app.screen, AccountPickerScreen)
        # 한 번 더 → 다시 원래 상태 (정확히 1회 토글이 보장되는지).
        tree.action_select_cursor()
        await pilot.pause()
        assert target.is_expanded is was_expanded
        assert isinstance(app.screen, AccountPickerScreen)


@pytest.mark.asyncio
async def test_picker_right_arrow_expands_or_descends():
    """CL #51096+: → 가 접힌 카테고리는 펼침, 펼친 카테고리는 첫 자식으로 이동."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import Tree

        await app.push_screen(AccountPickerScreen(app.session, side="left"))
        await pilot.pause()
        tree = app.screen.query_one("#acc-tree", Tree)
        cats = list(tree.root.children)
        expenses_cat = next(c for c in cats if c.data == "expenses")
        # 초기에는 첫 카테고리(자산)가 펼쳐져 있을 수 있음 — expenses 는 접힘
        assert expenses_cat.is_expanded is False
        # cursor 를 expenses 로 옮기고 → 누름
        tree.move_cursor(expenses_cat)
        await pilot.press("right")
        await pilot.pause()
        assert expenses_cat.is_expanded is True
        # cursor 그대로 (펼침만)
        assert tree.cursor_node is expenses_cat
        # 다시 → → 첫 자식 (식비) 으로 cursor 이동
        await pilot.press("right")
        await pilot.pause()
        assert tree.cursor_node.data == ("x20", "식비", "expenses")


@pytest.mark.asyncio
async def test_picker_left_arrow_collapses_or_ascends():
    """CL #51096+: ← 가 펼친 카테고리는 접음, leaf 는 부모로 이동."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import Tree

        await app.push_screen(
            AccountPickerScreen(app.session, side="left", current_id="x20"),
        )
        await pilot.pause()
        tree = app.screen.query_one("#acc-tree", Tree)
        cats = list(tree.root.children)
        expenses_cat = next(c for c in cats if c.data == "expenses")
        # current_id="x20" 이라 expenses 자동 펼침 + cursor 가 식비 leaf 위.
        assert expenses_cat.is_expanded is True
        assert tree.cursor_node.data == ("x20", "식비", "expenses")
        # leaf 위에서 ← 누르면 부모 카테고리로 cursor 이동, 펼침은 유지.
        await pilot.press("left")
        await pilot.pause()
        assert tree.cursor_node is expenses_cat
        assert expenses_cat.is_expanded is True
        # 카테고리 위에서 ← 누르면 접힘, cursor 유지.
        await pilot.press("left")
        await pilot.pause()
        assert expenses_cat.is_expanded is False
        assert tree.cursor_node is expenses_cat
        # 모달 그대로
        assert isinstance(app.screen, AccountPickerScreen)


@pytest.mark.asyncio
async def test_picker_leaf_enter_dismisses_with_account():
    """항목 leaf 위에서 Enter → dismiss((aid, title, type_key))."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        from textual.widgets import Tree

        results: list = []

        def _on_pick(r):
            results.append(r)

        await app.push_screen(
            AccountPickerScreen(app.session, side="left"),
            _on_pick,
        )
        await pilot.pause()
        tree = app.screen.query_one("#acc-tree", Tree)
        cats = list(tree.root.children)
        leaf = list(cats[1].children)[0]  # expenses → 식비
        app.screen.post_message(Tree.NodeSelected(leaf))
        await pilot.pause()
        assert results == [("x20", "식비", "expenses")]


# ---- CL #52906+ : 맥락 안내 (purpose) ---------------------------------


@pytest.mark.asyncio
async def test_picker_shows_purpose_when_provided():
    """`purpose` kwarg 가 주어지면 picker 안에 같은 텍스트가 보여야 — 사용자
    가 wizard 의 중간 단계에서 *왜* 이 picker 가 떴는지 즉시 알 수 있도록.
    """
    from textual.widgets import Static

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        await app.push_screen(
            AccountPickerScreen(
                app.session, side="right",
                purpose="명세서 거래 분류 — wizard 2/3",
            ),
        )
        await pilot.pause()
        # screen 의 _purpose attribute 가 caller 의 값 보관.
        assert "wizard 2/3" in str(app.screen._purpose)
        # hidden class 가 *없어야* 한다 — 사용자에게 보이는 상태.
        purpose_widget = app.screen.query_one("#picker-purpose", Static)
        assert "hidden" not in purpose_widget.classes


@pytest.mark.asyncio
async def test_picker_hides_purpose_when_not_provided():
    """`purpose` 미지정 — picker-purpose Static 이 hidden 클래스를 가져야."""
    from textual.widgets import Static

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_entries(app)
        await app.push_screen(
            AccountPickerScreen(app.session, side="left"),
        )
        await pilot.pause()
        purpose_widget = app.screen.query_one("#picker-purpose", Static)
        assert "hidden" in purpose_widget.classes
