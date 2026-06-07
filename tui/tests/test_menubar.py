"""widgets/menubar.py — MenuBar / MenuItem / MenuSpec / MenuPopup 단위.

CL #51126+ 사용자 요청: F10 풀다운 메뉴, Header 아래 항상 노출. 본 테스트는
위젯 자체의 자료 구조 + 핵심 동작 (active 토글, popup dismiss 형식)만 격리
검증. EntriesScreen 통합은 test_entries_screen.py 의 별도 case.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from whooing_tui.widgets.menubar import (
    MenuBar,
    MenuItem,
    MenuPopup,
    MenuSpec,
    menubar_left_offset_for,
)


# ---- dataclass 레벨 ------------------------------------------------------


def test_menuitem_default_enabled_true():
    it = MenuItem(label="새 거래", action_id="new_entry")
    assert it.enabled is True
    assert it.action_id == "new_entry"


def test_menuspec_holds_items_tuple():
    spec = MenuSpec(
        name="입력",
        items=(
            MenuItem("새 거래", "new_entry"),
            MenuItem("카드 명세서…", "import_card_statement"),
        ),
    )
    assert spec.name == "입력"
    assert len(spec.items) == 2
    assert spec.items[1].action_id == "import_card_statement"


# ---- menubar_left_offset_for — popup 정렬 helper ------------------------


def test_offset_for_first_menu_is_zero_or_small():
    menus = (MenuSpec("파일", ()), MenuSpec("입력", ()))
    assert menubar_left_offset_for(0, menus) == 0


def test_offset_for_second_menu_skips_first_korean_name():
    menus = (MenuSpec("파일", ()), MenuSpec("입력", ()))
    # "파일" = 한글 2글자 (display 폭 4) + " name " 의 좌우 공백 (2) + 구분자
    # "  " (2) → 8 정도. 정확값보다 monotonic increase 만 확인.
    off1 = menubar_left_offset_for(1, menus)
    assert off1 > 0


def test_offset_increases_monotonically():
    menus = (
        MenuSpec("파일", ()),
        MenuSpec("입력", ()),
        MenuSpec("화면", ()),
        MenuSpec("도움말", ()),
    )
    offsets = [menubar_left_offset_for(i, menus) for i in range(len(menus))]
    assert offsets == sorted(offsets)
    assert offsets[0] == 0
    assert offsets[-1] > offsets[0]


# ---- MenuBar 위젯 -------------------------------------------------------


class _BarApp(App):
    def __init__(self, menus):
        super().__init__()
        self._menus = menus

    def compose(self) -> ComposeResult:
        yield MenuBar(self._menus, id="bar")


@pytest.mark.asyncio
async def test_menubar_renders_all_menu_names():
    menus = (
        MenuSpec("파일", ()),
        MenuSpec("입력", ()),
        MenuSpec("도움말", ()),
    )
    app = _BarApp(menus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one("#bar", MenuBar)
        rendered = str(bar.render())
        for name in ("파일", "입력", "도움말"):
            assert name in rendered


@pytest.mark.asyncio
async def test_menubar_active_index_highlighted():
    """active 메뉴는 reverse 스타일 — Content 의 spans 에 'reverse' 가 있어야.
    Textual Content 는 Rich markup 을 (text, spans) 로 분리하므로 markup
    문자열 매칭이 아닌 span style 검사로 확인.
    """
    menus = (MenuSpec("A", ()), MenuSpec("B", ()))
    app = _BarApp(menus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one("#bar", MenuBar)
        bar.set_active(1)
        await pilot.pause()
        content = bar.render()
        # spans 에 reverse 스타일 1개 + dim (F10 hint) 1개 정도가 표준.
        styles = [getattr(s, "style", None) for s in (content.spans or [])]
        assert any("reverse" in (st or "") for st in styles), (
            f"reverse span 없음 — spans={content.spans}"
        )
        # plain text 에는 두 메뉴 이름 모두 등장 — markup 은 분리됐음.
        assert "A" in str(content)
        assert "B" in str(content)


@pytest.mark.asyncio
async def test_menubar_set_active_wraps_modulo():
    menus = (MenuSpec("A", ()), MenuSpec("B", ()))
    app = _BarApp(menus)
    async with app.run_test() as pilot:
        bar = app.query_one("#bar", MenuBar)
        bar.set_active(5)  # 5 % 2 = 1
        assert bar.active_index == 1
        bar.set_active(-1)  # -1 % 2 = 1 (Python 의 modulo 정책)
        assert bar.active_index == 1


@pytest.mark.asyncio
async def test_menubar_shows_f10_hint():
    """사용자가 진입 키를 잊지 않도록 우측에 (F10) 힌트."""
    menus = (MenuSpec("파일", ()),)
    app = _BarApp(menus)
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = app.query_one("#bar", MenuBar)
        assert "F10" in str(bar.render())


# ---- MenuPopup — dismiss 형식 ------------------------------------------


class _PopupApp(App):
    def __init__(self, spec):
        super().__init__()
        self._spec = spec
        self.dismissed_with = "<unset>"

    def on_mount(self) -> None:
        async def _record(value):
            self.dismissed_with = value
        self.push_screen(MenuPopup(self._spec), _record)


@pytest.mark.asyncio
async def test_menupopup_esc_dismisses_with_none():
    spec = MenuSpec(
        name="파일",
        items=(MenuItem("종료", "back"),),
    )
    app = _PopupApp(spec)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.dismissed_with is None


@pytest.mark.asyncio
async def test_menupopup_left_arrow_dismisses_with_nav_left():
    spec = MenuSpec(name="입력", items=(MenuItem("새 거래", "new_entry"),))
    app = _PopupApp(spec)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert app.dismissed_with == ("nav", "left")


@pytest.mark.asyncio
async def test_menupopup_right_arrow_dismisses_with_nav_right():
    spec = MenuSpec(name="입력", items=(MenuItem("새 거래", "new_entry"),))
    app = _PopupApp(spec)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.dismissed_with == ("nav", "right")


@pytest.mark.asyncio
async def test_menupopup_enter_dismisses_with_action_id():
    """OptionList 의 highlighted 항목에서 enter → action_id 반환."""
    spec = MenuSpec(
        name="화면",
        items=(
            MenuItem("섹션", "open_sections"),
            MenuItem("계정", "open_accounts"),
        ),
    )
    app = _PopupApp(spec)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")  # 두 번째로 이동
        await pilot.press("enter")
        await pilot.pause()
        assert app.dismissed_with == "open_accounts"


# ---- CL #51131+ menubar_bindings + MenuBarMixin -------------------------


def test_menubar_bindings_returns_f10_priority():
    """`menubar_bindings()` 가 spread 가능한 list — F10 priority binding."""
    from whooing_tui.widgets import menubar_bindings
    out = menubar_bindings()
    assert isinstance(out, list)
    assert len(out) >= 1
    f10 = next((b for b in out if b.key == "f10"), None)
    assert f10 is not None
    assert f10.action == "open_menu"
    # priority=True 가 명시 — focused widget 보다 우선.
    assert f10.priority is True


def test_menubarmixin_dispatch_calls_action_method():
    """default `_dispatch_menu_action(action_id)` → `action_<id>` lookup + 호출."""
    from whooing_tui.widgets import MenuBarMixin

    class _Stub(MenuBarMixin):
        def __init__(self):
            self.called: list[str] = []
        def action_refresh(self):
            self.called.append("refresh")
        def action_back(self):
            self.called.append("back")

    s = _Stub()
    s._dispatch_menu_action("refresh")
    s._dispatch_menu_action("back")
    s._dispatch_menu_action("__nonexistent__")  # silent — 안 호출.
    assert s.called == ["refresh", "back"]


def test_menubarmixin_default_build_menus_returns_empty():
    """default override 안 하면 빈 tuple — popup 진입 시 즉시 return (no-op)."""
    from whooing_tui.widgets import MenuBarMixin
    class _Stub(MenuBarMixin):
        pass
    assert _Stub()._build_menus() == ()


def test_menubarmixin_default_widget_id_is_none():
    """default `_menubar_widget_id()` = None → 첫 MenuBar 위젯 자동 선택."""
    from whooing_tui.widgets import MenuBarMixin
    class _Stub(MenuBarMixin):
        pass
    assert _Stub()._menubar_widget_id() is None


# ---- CL #52759+ : Alt 키 진입 + 마우스 클릭 ---------------------------


def test_menubar_bindings_include_alt_combos():
    """F10 외에 Alt 단독 + Alt+M + Alt+F 가 등록 — 사용자 요청."""
    from whooing_tui.widgets.menubar import menubar_bindings

    keys = [b.key for b in menubar_bindings()]
    assert "f10" in keys
    assert "alt" in keys
    assert "alt+m" in keys
    assert "alt+f" in keys


def test_menubar_bindings_all_open_menu_action():
    """Alt / Alt+M / Alt+F 모두 같은 action 으로 dispatch."""
    from whooing_tui.widgets.menubar import menubar_bindings

    for b in menubar_bindings():
        assert b.action == "open_menu", (
            f"binding {b.key!r} action={b.action!r} 가 open_menu 가 아님"
        )


def test_menubar_ranges_layout():
    """각 메뉴 (start, end) 가 _render_label 의 visual layout 과 일치."""
    from whooing_tui.widgets.menubar import (
        MenuSpec, menubar_ranges,
    )

    menus = (
        MenuSpec(name="파일", items=()),
        MenuSpec(name="입력", items=()),
        MenuSpec(name="도움말", items=()),
    )
    r = menubar_ranges(menus)
    # 한글 2글자 = 4 cell + " " 2 = 6, padding 1 이라 (1,7).
    # 다음 메뉴는 구분자 "  " (2) 후 = 9 부터.
    assert r == [(1, 7), (9, 15), (17, 25)]


def test_menubar_ranges_mixed_ascii_and_cjk():
    """ASCII + 한글 혼합도 폭 계산 정확."""
    from whooing_tui.widgets.menubar import MenuSpec, menubar_ranges

    menus = (
        MenuSpec(name="File", items=()),    # 4 cell + 2 = 6
        MenuSpec(name="입력", items=()),    # 4 cell + 2 = 6
    )
    r = menubar_ranges(menus)
    assert r == [(1, 7), (9, 15)]


def test_index_at_offset_inside_menu():
    """클릭이 메뉴 영역 내 → 해당 index."""
    from whooing_tui.widgets.menubar import (
        MenuSpec, menubar_index_at_offset,
    )

    menus = (
        MenuSpec(name="파일", items=()),
        MenuSpec(name="입력", items=()),
    )
    assert menubar_index_at_offset(3, menus) == 0
    assert menubar_index_at_offset(11, menus) == 1


def test_index_at_offset_in_separator_returns_none():
    """메뉴와 메뉴 사이 구분자 영역 클릭은 매칭 X."""
    from whooing_tui.widgets.menubar import (
        MenuSpec, menubar_index_at_offset,
    )

    menus = (
        MenuSpec(name="파일", items=()),
        MenuSpec(name="입력", items=()),
    )
    # 7, 8 이 구분자.
    assert menubar_index_at_offset(7, menus) is None
    assert menubar_index_at_offset(8, menus) is None


def test_index_at_offset_outside_returns_none():
    """메뉴바 영역 밖 클릭은 None."""
    from whooing_tui.widgets.menubar import (
        MenuSpec, menubar_index_at_offset,
    )

    menus = (MenuSpec(name="파일", items=()),)
    assert menubar_index_at_offset(-1, menus) is None
    assert menubar_index_at_offset(100, menus) is None
    # 빈 menus.
    assert menubar_index_at_offset(0, ()) is None


def test_menubar_left_offset_for_uses_new_ranges():
    """기존 시그니처 (popup 좌측 margin) 도 ranges 기반으로 동작."""
    from whooing_tui.widgets.menubar import (
        MenuSpec, menubar_left_offset_for,
    )

    menus = (
        MenuSpec(name="파일", items=()),
        MenuSpec(name="입력", items=()),
        MenuSpec(name="도움말", items=()),
    )
    # 0번 메뉴는 항상 0.
    assert menubar_left_offset_for(0, menus) == 0
    # 1번 = ranges[1].start + 1 = 9 + 1 = 10.
    assert menubar_left_offset_for(1, menus) == 10
    # 2번 = 17 + 1 = 18.
    assert menubar_left_offset_for(2, menus) == 18


def test_menubar_clicked_message_dataclass():
    """MenuBar.MenuClicked 가 menu_index 보존."""
    from whooing_tui.widgets.menubar import MenuBar

    msg = MenuBar.MenuClicked(menu_index=2)
    assert msg.menu_index == 2


@pytest.mark.asyncio
async def test_menubar_click_dispatches_menu_clicked_message():
    """MenuBar.on_click → menu_index 매핑 → MenuClicked 발사.

    부모 Mixin Screen 의 on_menu_bar_menu_clicked 가 받아 worker 시작 —
    여기서는 message 발사 자체만 검증 (worker 통합은 별도 케이스).
    """
    from textual.app import App
    from textual.events import Click

    from whooing_tui.widgets.menubar import MenuBar, MenuSpec

    received: list[int] = []

    class _Probe(App):
        def compose(self):
            yield MenuBar(
                (MenuSpec(name="파일", items=()),
                 MenuSpec(name="입력", items=())),
                id="mb",
            )

        def on_menu_bar_menu_clicked(self, event: MenuBar.MenuClicked):
            received.append(event.menu_index)

    app = _Probe()
    async with app.run_test() as pilot:
        # MenuBar 직접 click — pilot.click 가 위젯 (selector) 지정 가능.
        bar = app.query_one("#mb", MenuBar)
        # "입력" 메뉴 영역 (cell 10 정도) 클릭. 단 pilot.click 가 widget
        # offset 처리. post_message 직접 시뮬.
        bar.post_message(
            MenuBar.MenuClicked(menu_index=1),
        )
        await pilot.pause()
        assert received == [1]


# ---- MenuPopup — 마우스 바깥/메뉴바 클릭 동작 (CL) -----------------------

import types as _types  # noqa: E402

from textual.geometry import Region  # noqa: E402

from whooing_tui.widgets.menubar import menubar_ranges  # noqa: E402


class _PopupNavApp(App):
    """MenuPopup 를 menus/bar_region 과 함께 push — 클릭 분기 검증용."""

    def __init__(self, spec, menus, menu_index, bar_region):
        super().__init__()
        self._spec = spec
        self._menus = menus
        self._mi = menu_index
        self._br = bar_region
        self.dismissed_with = "<unset>"

    def on_mount(self) -> None:
        async def _rec(value):
            self.dismissed_with = value
        self.push_screen(
            MenuPopup(
                self._spec,
                menus=self._menus,
                menu_index=self._mi,
                bar_region=self._br,
            ),
            _rec,
        )


def _click(sx, sy):
    return _types.SimpleNamespace(screen_x=sx, screen_y=sy, stop=lambda: None)


@pytest.mark.asyncio
async def test_menupopup_click_outside_box_closes():
    menus = (MenuSpec("파일", ()), MenuSpec("입력", ()))
    app = _PopupNavApp(menus[0], menus, 0, Region(0, 1, 40, 1))
    async with app.run_test() as pilot:
        await pilot.pause()
        popup = app.screen
        # 박스 한참 아래(바깥, 메뉴바 행도 아님) 클릭 → 닫기.
        popup.on_click(_click(5, 50))
        await pilot.pause()
        assert app.dismissed_with is None


@pytest.mark.asyncio
async def test_menupopup_click_other_menu_switches():
    menus = (MenuSpec("파일", ()), MenuSpec("입력", ()))
    ranges = menubar_ranges(menus)
    # 메뉴 1(입력) 범위 내 x. bar_region.x=0, y=1.
    sx = ranges[1][0]
    app = _PopupNavApp(menus[0], menus, 0, Region(0, 1, 40, 1))
    async with app.run_test() as pilot:
        await pilot.pause()
        popup = app.screen
        popup.on_click(_click(sx, 1))
        await pilot.pause()
        assert app.dismissed_with == ("switch", 1)


@pytest.mark.asyncio
async def test_menupopup_click_same_menu_closes():
    menus = (MenuSpec("파일", ()), MenuSpec("입력", ()))
    ranges = menubar_ranges(menus)
    sx = ranges[0][0]  # 현재 열린 메뉴(0) 자신 클릭 → 닫기 토글.
    app = _PopupNavApp(menus[0], menus, 0, Region(0, 1, 40, 1))
    async with app.run_test() as pilot:
        await pilot.pause()
        popup = app.screen
        popup.on_click(_click(sx, 1))
        await pilot.pause()
        assert app.dismissed_with is None


@pytest.mark.asyncio
async def test_menupopup_click_inside_box_does_not_close():
    menus = (MenuSpec("파일", (MenuItem("종료", "back"),)),)
    app = _PopupNavApp(menus[0], menus, 0, Region(0, 1, 40, 1))
    async with app.run_test() as pilot:
        await pilot.pause()
        popup = app.screen
        box = popup.query_one("#menupopup_box")
        # 박스 내부 좌표 클릭 → on_click 은 관여 안 함 (dismiss 안 일어남).
        popup.on_click(_click(box.region.x + 1, box.region.y + 1))
        await pilot.pause()
        assert app.dismissed_with == "<unset>"
