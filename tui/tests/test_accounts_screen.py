"""AccountsScreen + AccountEditDialog — Textual App.run_test() 통합.

EntriesScreen 에서 'a' 로 push 한 뒤 CRUD 흐름:
  - n (new): AccountEditDialog → dismiss(AccountDraft) → create_account
  - Enter (edit): 선택된 계정과목으로 dialog → update_account
  - d (delete): check_account_deletable + ConfirmModal → delete_account
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError
from whooing_tui.screens.accounts import (
    AccountDraft, AccountEditDialog, AccountsScreen,
)
from whooing_tui.screens.edit_entry import ConfirmModal
from whooing_tui.screens.entries import EntriesScreen


class FakeClient:
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts: dict[str, list[dict[str, Any]]] = {
            "expenses": [
                {
                    "account_id": "x20", "title": "식비",
                    "open_date": "20240101", "close_date": "29991231",
                    "category": "floating", "memo": "", "type": "account",
                },
                {
                    "account_id": "x21", "title": "교통비",
                    "open_date": "20240101", "close_date": "29991231",
                    "category": "floating", "memo": "", "type": "account",
                },
            ],
            "assets": [
                {
                    "account_id": "x11", "title": "현금",
                    "open_date": "20240101", "close_date": "29991231",
                    "category": "normal", "memo": "", "type": "account",
                },
            ],
        }
        self.create_account_calls: list[dict[str, Any]] = []
        self.update_account_calls: list[dict[str, Any]] = []
        self.delete_account_calls: list[dict[str, Any]] = []
        self.check_calls: list[dict[str, Any]] = []
        self.check_result: dict[str, Any] = {
            "entries_count": 0, "balance": 0, "is_last": False,
        }
        self.create_account_error: ToolError | None = None
        self.update_account_error: ToolError | None = None
        self.delete_account_error: ToolError | None = None

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return {k: list(v) for k, v in self.accounts.items()}

    async def list_entries(self, section_id, start, end):
        return []

    async def create_account(self, **kwargs):
        if self.create_account_error:
            raise self.create_account_error
        self.create_account_calls.append(kwargs)
        new = {
            "account_id": f"new-{len(self.create_account_calls)}",
            "title": kwargs["title"],
            "open_date": kwargs["open_date"],
            "close_date": kwargs.get("close_date") or "29991231",
            "type": kwargs["type"],
            "category": kwargs.get("category", ""),
            "memo": kwargs.get("memo", ""),
        }
        self.accounts.setdefault(kwargs["account"], []).append(new)
        return {"account_id": new["account_id"]}

    async def update_account(self, **kwargs):
        if self.update_account_error:
            raise self.update_account_error
        self.update_account_calls.append(kwargs)
        for items in self.accounts.values():
            for a in items:
                if a["account_id"] == kwargs["account_id"]:
                    a["title"] = kwargs["title"]
                    a["open_date"] = kwargs["open_date"]
                    a["close_date"] = kwargs["close_date"]
                    return {"account_id": a["account_id"]}
        return {}

    async def delete_account(self, **kwargs):
        if self.delete_account_error:
            raise self.delete_account_error
        self.delete_account_calls.append(kwargs)
        for k, items in list(self.accounts.items()):
            self.accounts[k] = [
                a for a in items if a["account_id"] != kwargs["account_id"]
            ]
        return {}

    async def check_account_deletable(self, **kwargs):
        self.check_calls.append(kwargs)
        return dict(self.check_result)


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def _open_accounts(app, pilot) -> AccountsScreen:
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1"
        and app.session.id_of("식비") == "x20",
        timeout=3.0,
    )
    es: EntriesScreen = app.screen  # type: ignore[assignment]
    es.action_open_accounts()
    await pilot.pause()
    await _wait_for(
        lambda: isinstance(app.screen, AccountsScreen), timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


# ---- CL #51131+ AccountsScreen 메뉴바 통합 -----------------------------


@pytest.mark.asyncio
async def test_accounts_screen_has_menubar():
    """AccountsScreen 도 Header 아래 MenuBar 가 보여야 함."""
    from whooing_tui.widgets import MenuBar
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        screen = await _open_accounts(app, pilot)
        bar = screen.query_one("#accounts-menubar", MenuBar)
        rendered = str(bar.render())
        assert "파일" in rendered
        assert "입력" in rendered
        assert "도움말" in rendered


@pytest.mark.asyncio
async def test_accounts_screen_f10_opens_menu():
    """F10 → AccountsScreen 의 첫 메뉴 popup."""
    from whooing_tui.widgets.menubar import MenuPopup
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _open_accounts(app, pilot)
        await pilot.press("f10")
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup)
        assert app.screen.spec.name == "파일"


def test_accounts_screen_menu_includes_new_account():
    """메뉴 정의에 new_account 항목 — wiring 안전망."""
    menus = AccountsScreen._build_menus()
    flat = [(m.name, it.action_id) for m in menus for it in m.items]
    assert any(action == "new_account" for _, action in flat)
    assert any(action == "refresh" for _, action in flat)
    assert any(action == "back" for _, action in flat)


@pytest.mark.asyncio
async def test_accounts_menu_dispatch_calls_action_refresh():
    """메뉴 dispatch('refresh') → AccountsScreen.action_refresh 호출
    (mixin default — `action_<id>` lookup). 검증은 spy 로 직접.
    """
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        screen = await _open_accounts(app, pilot)
        called = {"yes": False}
        screen.action_refresh = lambda: called.update(yes=True)  # type: ignore[method-assign]
        screen._dispatch_menu_action("refresh")
        await pilot.pause()
        assert called["yes"] is True


@pytest.mark.asyncio
async def test_accounts_menu_dispatch_calls_action_back():
    """메뉴 dispatch('back') → AccountsScreen.action_back → pop_screen → EntriesScreen."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        screen = await _open_accounts(app, pilot)
        screen._dispatch_menu_action("back")
        await pilot.pause()
        # back → pop → EntriesScreen 으로.
        assert isinstance(app.screen, EntriesScreen)


# ---- 트리 렌더 + 진입 흐름 ---------------------------------------------


@pytest.mark.asyncio
async def test_accounts_screen_pushes_and_renders_tree():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        # Tree root + type 노드들 + leaf account 노드들이 만들어졌는지
        from textual.widgets import Tree
        tree = scr.query_one("#accounts-tree", Tree)
        # 루트는 reset 으로 표시됐고, 자식 type 노드 (assets / expenses) 가 있다
        # textual API: tree.root.children — type 노드들
        type_labels = [str(c.label) for c in tree.root.children]
        assert any("자산" in lbl for lbl in type_labels)
        assert any("지출" in lbl for lbl in type_labels)


# ---- new (create) ------------------------------------------------------


@pytest.mark.asyncio
async def test_new_account_action_opens_dialog():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        scr.action_new_account()
        await pilot.pause()
        assert isinstance(app.screen, AccountEditDialog)


@pytest.mark.asyncio
async def test_new_account_dismiss_calls_create_account():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        scr.action_new_account()
        await pilot.pause()
        # 사용자가 폼 채운 결과 dismiss
        draft = AccountDraft(
            account="expenses", type="account",
            title="문화생활", open_date="20240301",
            close_date="29991231",
            category="floating", memo="영화/공연",
        )
        app.screen.dismiss(draft)
        ok = await _wait_for(
            lambda: len(fake.create_account_calls) == 1, timeout=3.0,
        )
        assert ok
        call = fake.create_account_calls[0]
        assert call["section_id"] == "s1"
        assert call["account"] == "expenses"
        assert call["type"] == "account"
        assert call["title"] == "문화생활"
        assert call["open_date"] == "20240301"
        assert call["category"] == "floating"
        assert call["memo"] == "영화/공연"


# ---- edit (update) -----------------------------------------------------


@pytest.mark.asyncio
async def test_edit_account_action_requires_selected_leaf():
    """루트나 type 노드 cursor 에서는 'edit' 거부 (status error)."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        # 트리는 root cursor 로 시작 — leaf 가 아니라 거부
        scr.action_edit_account()
        await pilot.pause()
        # AccountEditDialog 가 떠선 안 됨
        assert not isinstance(app.screen, AccountEditDialog)
        assert "선택된 항목이 없거나" in scr.last_status


@pytest.mark.asyncio
async def test_edit_account_with_leaf_cursor_calls_update():
    """leaf node cursor 로 직접 옮긴 후 dismiss(draft) → update_account."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        # cursor 를 leaf 노드 (식비 = x20) 로 이동
        from textual.widgets import Tree
        tree = scr.query_one("#accounts-tree", Tree)
        # type 노드 (지출) 의 첫 leaf 를 찾아 select
        target_leaf = None
        for type_node in tree.root.children:
            if isinstance(type_node.data, dict) and type_node.data.get("type_key") == "expenses":
                if type_node.children:
                    target_leaf = type_node.children[0]
                    break
        assert target_leaf is not None
        tree.select_node(target_leaf)
        await pilot.pause()

        scr.action_edit_account()
        await pilot.pause()
        assert isinstance(app.screen, AccountEditDialog)

        draft = AccountDraft(
            account="expenses", type="account",
            title="식비_수정", open_date="20240101",
            close_date="29991231",
            category="floating", memo="",
            account_id="x20",
        )
        app.screen.dismiss(draft)
        ok = await _wait_for(
            lambda: len(fake.update_account_calls) == 1, timeout=3.0,
        )
        assert ok
        call = fake.update_account_calls[0]
        assert call["account_id"] == "x20"
        assert call["title"] == "식비_수정"


# ---- delete + check_deletable ------------------------------------------


@pytest.mark.asyncio
async def test_delete_account_runs_check_then_confirm_then_delete():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        # leaf 선택
        from textual.widgets import Tree
        tree = scr.query_one("#accounts-tree", Tree)
        target = None
        for type_node in tree.root.children:
            if isinstance(type_node.data, dict) and type_node.data.get("type_key") == "expenses":
                target = type_node.children[0]
                break
        tree.select_node(target)
        await pilot.pause()

        scr.action_delete_account()
        # check_deletable 호출 → ConfirmModal push 까지
        ok = await _wait_for(
            lambda: len(fake.check_calls) == 1
            and isinstance(app.screen, ConfirmModal),
            timeout=3.0,
        )
        assert ok
        check_call = fake.check_calls[0]
        assert check_call["account_id"] == "x20"
        assert check_call["account"] == "expenses"

        # 사용자가 yes 로 확정
        app.screen.dismiss(True)
        ok2 = await _wait_for(
            lambda: len(fake.delete_account_calls) == 1, timeout=3.0,
        )
        assert ok2
        del_call = fake.delete_account_calls[0]
        assert del_call["account_id"] == "x20"
        assert del_call["account"] == "expenses"


@pytest.mark.asyncio
async def test_delete_account_cancel_keeps_record():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        from textual.widgets import Tree
        tree = scr.query_one("#accounts-tree", Tree)
        target = None
        for type_node in tree.root.children:
            if isinstance(type_node.data, dict) and type_node.data.get("type_key") == "expenses":
                target = type_node.children[0]
                break
        tree.select_node(target)
        await pilot.pause()

        scr.action_delete_account()
        await _wait_for(
            lambda: isinstance(app.screen, ConfirmModal), timeout=3.0,
        )
        # No
        app.screen.dismiss(False)
        await pilot.pause()
        # delete_account 호출 안 함
        assert fake.delete_account_calls == []


@pytest.mark.asyncio
async def test_delete_account_warns_on_existing_entries():
    """check_deletable 결과에 entries_count > 0 이면 ConfirmModal 메시지에 경고."""
    fake = FakeClient()
    fake.check_result = {"entries_count": 12, "balance": 50000, "is_last": False}
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        from textual.widgets import Tree
        tree = scr.query_one("#accounts-tree", Tree)
        target = None
        for type_node in tree.root.children:
            if isinstance(type_node.data, dict) and type_node.data.get("type_key") == "expenses":
                target = type_node.children[0]
                break
        tree.select_node(target)
        await pilot.pause()

        scr.action_delete_account()
        await _wait_for(
            lambda: isinstance(app.screen, ConfirmModal), timeout=3.0,
        )
        # ConfirmModal 의 메시지 attribute 검증 (없을 수도 — 단순히 모달이 떴는지만)
        # 이 테스트는 메시지가 있다는 것만 verify하는 게 안전.
        assert app.screen._message  # noqa: SLF001
        assert "12" in app.screen._message  # entries_count
        assert "50,000" in app.screen._message  # balance


# ---- 빈 cursor + back -------------------------------------------------


@pytest.mark.asyncio
async def test_back_returns_to_entries():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        scr = await _open_accounts(app, pilot)
        scr.action_back()
        ok = await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen), timeout=2.0,
        )
        assert ok
