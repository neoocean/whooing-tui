"""풀다운 메뉴바 — Header 아래 항상 노출 + F10 진입.

CL #51126+. 사용자 요청:
> 기존 보고서 등 여러 기능이 추가될 예정이기 때문에 tui 앱의 전통을 따라
> 풀다운 메뉴 형식으로 인터페이스를 구성해주세요. 풀다운 메뉴는 F10 키로
> 열 수 있고 앱 타이틀바 아래에 항상 노출되어 있어야 합니다.

설계:
- `MenuBar(Static)` — Header 와 본문 사이의 1-row 위젯. 메뉴 이름 (파일 /
  입력 / ...) 을 공백으로 구분해 한 줄 표시. 항상 visible.
- `MenuPopup(ModalScreen)` — F10 또는 ←/→ 으로 활성화된 메뉴의 항목들을
  세로 OptionList 로 표시. 화면 좌상단 고정 (메뉴바 바로 아래).
- 액션 dispatch 는 `MenuItem.action_id` (string) 로 caller 에게 위임 —
  본 위젯은 *선택* 만 책임지고 의미적 실행은 화면이 한다 (소프트 결합).

키 처리 정책:
- F10 (또는 ESC 가 아닌 다른 글로벌 트리거) — 화면 BINDINGS 가 잡고
  `MenuPopup` push.
- MenuPopup 내부:
  * ↑/↓: 항목 이동.
  * Enter: 선택 (action_id 와 함께 dismiss).
  * ←/→: 다른 메뉴로 (현재 메뉴 dismiss + 다음 메뉴 popup — caller 가 처리).
  * Esc: 취소 (None dismiss).

본 모듈은 외부 의존 0 (Textual 만). 단위 테스트도 Textual 의 run_test 만 사용.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class MenuItem:
    """단일 메뉴 항목.

    label: 사용자에게 보일 텍스트 (한글). 끝에 단축키 hint 를 `(key)` 로
      넣을 수 있음 (예: "새 거래 (n)"). hint 자체는 본 위젯이 해석하지 않음 —
      화면이 BINDINGS 로 별도로 처리.
    action_id: 화면이 `dispatch_menu_action(action_id)` 같은 hook 으로
      받아 실행할 식별자 (예: "new_entry", "import_card_statement").
    enabled: False 면 OptionList 에서 disabled 로 표시 (사용자가 못 고름).
    """

    label: str
    action_id: str
    enabled: bool = True


@dataclass(frozen=True)
class MenuSpec:
    """단일 메뉴 (예: "입력") + 그 항목들."""

    name: str  # 메뉴바에 표시될 짧은 이름.
    items: tuple[MenuItem, ...]  # 항목 list. 빈 tuple 이면 메뉴 자체 disabled.


class MenuBar(Static):
    """Header 아래 1-row 메뉴바. 항상 visible.

    `menus` 는 `tuple[MenuSpec, ...]` 로 compose 시 주입. 활성 메뉴 인덱스
    `_active_index` 는 시각상 강조 (배경 강조) — F10 직후의 popup 대상.
    실제 popup 은 caller 가 `MenuPopup` 으로 push.

    CL #52759+: 마우스 클릭으로도 메뉴 열기 — `MenuClicked` message 를
    부모 (MenuBarMixin Screen) 가 listen.
    """

    class MenuClicked(Message):
        """마우스 클릭으로 메뉴 진입 요청. menu_index = 클릭된 메뉴 위치."""

        def __init__(self, menu_index: int) -> None:
            super().__init__()
            self.menu_index = menu_index

    DEFAULT_CSS = """
    MenuBar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        menus: Iterable[MenuSpec],
        *,
        active_index: int = 0,
        id: str | None = None,
    ) -> None:
        # Static 의 renderable 을 빈 문자열로 초기화 — _render_label 가 update.
        super().__init__("", id=id)
        self.menus: tuple[MenuSpec, ...] = tuple(menus)
        self._active_index = active_index

    def on_click(self, event: events.Click) -> None:
        """CL #52759+: 마우스 클릭 — 클릭한 메뉴 영역 → MenuClicked 발사.

        click 의 x 좌표 (위젯 안 cell offset) 를 `menubar_index_at_offset`
        로 menu index 로 매핑. 영역 밖 클릭은 noop.
        """
        idx = menubar_index_at_offset(event.x, self.menus)
        if idx is None:
            return
        self.post_message(self.MenuClicked(idx))
        event.stop()

    def on_mount(self) -> None:
        self._render_label()

    def set_active(self, index: int) -> None:
        """활성 메뉴 인덱스 변경 — 시각 갱신."""
        if not self.menus:
            return
        self._active_index = index % len(self.menus)
        self._render_label()

    @property
    def active_index(self) -> int:
        return self._active_index

    def _render_label(self) -> None:
        """메뉴 이름들을 한 줄로 — 활성 메뉴는 reverse 로 강조.

        한글 폭 (CJK wide) 을 고려해 단순 공백 구분. Rich markup 사용 —
        활성 메뉴만 `[reverse]...[/]` 로 감싸 색 반전.
        """
        if not self.menus:
            self.update("")
            return
        parts = []
        for i, m in enumerate(self.menus):
            if i == self._active_index:
                parts.append(f"[reverse] {m.name} [/]")
            else:
                parts.append(f" {m.name} ")
        # F10 안내를 우측에 — 사용자가 진입 키를 잊지 않게.
        self.update("  ".join(parts) + "    [dim](F10)[/]")


class MenuPopup(ModalScreen[tuple[int, str] | str | None]):
    """단일 메뉴의 항목 popup. dismiss 값:

    - `("nav", "left")` / `("nav", "right")` — 사용자가 ←/→ 로 다른 메뉴 요청.
    - `str` — 선택된 `action_id`.
    - `None` — Esc 로 취소.

    화면 (caller) 은 dismiss 값을 보고 다음 popup 을 push 하거나 액션 dispatch.
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("left", "nav_left", "←"),
        Binding("right", "nav_right", "→"),
    ]

    DEFAULT_CSS = """
    MenuPopup {
        align: left top;
        /* CL #52773+: backdrop 제거 — modal frame 만 보이고 뒷 화면은
           원래 색 그대로 (사용자 요청: 메뉴가 화면 하단까지 길게 가리지
           않게). textual ModalScreen 의 default background 가 50% black
           overlay 라 메뉴 외 영역까지 어두워졌었다. */
        background: transparent;
    }
    #menupopup_box {
        background: $panel;
        border: solid $primary;
        padding: 0 1;
        width: auto;
        /* CL #52814+: min-width 제거 (was 20) — 사용자 요청: 텍스트 폭에
           맞춰 좁힌다. max-width 만 안전망으로 유지. OptionList 의 width:
           auto 와 함께 컨테이너가 항목 최대 폭에 fit. */
        max-width: 60;
        height: auto;       /* 항목 수만큼만 — 화면 하단까지 늘어나지 않음. */
        margin-top: 2;     /* Header(1) + MenuBar(1) 아래로. */
        margin-left: 2;
    }
    #menupopup_title {
        color: $accent;
        text-style: bold;
        height: 1;
        width: auto;
    }
    #menupopup_list {
        height: auto;
        max-height: 20;
        width: auto;
    }
    """

    def __init__(
        self,
        spec: MenuSpec,
        *,
        margin_left: int = 2,
    ) -> None:
        super().__init__()
        self.spec = spec
        self._margin_left = margin_left

    def compose(self) -> ComposeResult:
        with Container(id="menupopup_box"):
            yield Static(self.spec.name, id="menupopup_title")
            options = []
            for it in self.spec.items:
                # disabled 항목은 OptionList 에서 dim + un-selectable.
                opt = Option(
                    it.label,
                    id=it.action_id,
                    disabled=not it.enabled,
                )
                options.append(opt)
            yield OptionList(*options, id="menupopup_list")

    def on_mount(self) -> None:
        # 좌측 margin 을 동적으로 — caller 가 메뉴 위치에 맞춰 align.
        try:
            box = self.query_one("#menupopup_box")
            box.styles.margin = (2, 0, 0, max(0, self._margin_left))
        except Exception:  # pragma: no cover
            pass
        # 첫 항목으로 cursor.
        try:
            self.query_one("#menupopup_list", OptionList).focus()
        except Exception:  # pragma: no cover
            pass

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        """Enter 또는 클릭으로 항목 선택 — action_id 반환."""
        opt = event.option
        if opt.id is None:  # pragma: no cover — 모든 항목에 id 주입.
            return
        self.dismiss(opt.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_nav_left(self) -> None:
        self.dismiss(("nav", "left"))

    def action_nav_right(self) -> None:
        self.dismiss(("nav", "right"))


def _menu_display_width(name: str) -> int:
    """메뉴 이름의 표시 폭 — 한글/CJK 는 2 cell, ASCII 는 1 cell, + " name " 의 좌우 공백 2."""
    return sum(2 if ord(ch) > 0x7F else 1 for ch in name) + 2


def menubar_ranges(menus: tuple[MenuSpec, ...]) -> list[tuple[int, int]]:
    """각 메뉴의 (start_col, end_col) 범위 — end 는 exclusive.

    `_render_label` 의 형식 `"  ".join(" name ")` 를 그대로 재현. 마우스
    클릭 좌표 → menu index 매핑에 사용 (`menubar_index_at_offset`).
    """
    ranges: list[tuple[int, int]] = []
    # `MenuBar` 의 padding: `0 1` — 좌측 1 셀.
    cur = 1
    for i, m in enumerate(menus):
        w = _menu_display_width(m.name)
        ranges.append((cur, cur + w))
        cur += w + 2  # 구분자 "  " (공백 2).
    return ranges


def menubar_index_at_offset(
    x: int, menus: tuple[MenuSpec, ...],
) -> int | None:
    """클릭한 x col → menu index. 메뉴 영역 밖이면 None.

    CL #52759+: `MenuBar.on_click` 가 사용. event.x 는 위젯 좌상단 기준
    cell offset.
    """
    if not menus or x < 0:
        return None
    for i, (start, end) in enumerate(menubar_ranges(menus)):
        if start <= x < end:
            return i
    return None


def menubar_left_offset_for(menu_index: int, menus: tuple[MenuSpec, ...]) -> int:
    """메뉴바에서 N번째 메뉴 이름의 시작 column 추정 — popup 의 좌측 margin.

    내부적으로 `menubar_ranges` 의 첫 col +1 (box margin baseline 보정).
    기존 호출자 (`_open_menu_loop_async`) 와의 후방 호환을 위해 시그니처
    유지.
    """
    if menu_index <= 0 or not menus:
        return 0
    ranges = menubar_ranges(menus)
    if menu_index >= len(ranges):
        return ranges[-1][0] + 1 if ranges else 0
    return ranges[menu_index][0] + 1


# ---- CL #51131+ MenuBarMixin — Screen 확장용 -----------------------------


def menubar_bindings() -> list[Binding]:
    """`MenuBarMixin` 사용 시 caller 가 BINDINGS 에 spread 해야 하는 키들.

    Textual 의 `_merged_bindings` 가 mixin (object 직속 상속) 의 BINDINGS 를
    walk 하지 않으므로 mixin 자체에 BINDINGS 를 두면 무효 — 각 화면이 명시
    spread 필요.

    사용 예 (`EntriesScreen.BINDINGS`):
        BINDINGS = [
            *menubar_bindings(),
            # ... 화면별 키들 ...
        ]

    진입 키 (CL #52759+):
      - **F10** — 기본. Footer 에 노출. IME 영향 없는 기능키, priority.
      - **Alt** 단독 — Windows 의 전통 메뉴 진입 키. terminal 이 modifier
        단독 event 를 보내는 환경 (일부 X11 / Win Terminal) 에서 작동.
      - **Alt+M** / **Alt+F** — 항상 작동하는 modifier combo. M = Menu,
        F = File. macOS Option 키도 같은 escape sequence 라 동등.
      - 마우스 클릭 — `MenuBar.on_click` 이 `MenuClicked` message 발사 →
        `MenuBarMixin.on_menu_bar_menu_clicked` 핸들러가 popup 호출.
    """
    return [
        Binding("f10", "open_menu", "Menu", show=True, priority=True),
        # CL #52759+: Alt 단독 — 사용자 요청. terminal 이 modifier 단독을
        # 보낼 때만 발화. macOS Terminal/iTerm 의 Option 단독은 보통 무시
        # 됨 — 그 환경은 Alt+M / Alt+F 또는 마우스 클릭 사용.
        Binding("alt", "open_menu", "", show=False, priority=True),
        # Alt+M (Menu) / Alt+F (File) — 모든 환경에서 작동하는 fallback.
        Binding("alt+m", "open_menu", "", show=False, priority=True),
        Binding("alt+f", "open_menu", "", show=False, priority=True),
    ]


class MenuBarMixin:
    """일반 Screen 에 F10 풀다운 메뉴바를 부여하는 mixin.

    상속 패턴:
      class MyScreen(MenuBarMixin, Screen):
          BINDINGS = [
              *menubar_bindings(),
              # ... screen 별 키들 ...
          ]
          def _build_menus(self): return (MenuSpec(...), ...)
          def compose(self):
              yield Header()
              yield MenuBar(self._build_menus(), id="myscreen-menubar")
              # ... 본문 ...

    `_dispatch_menu_action(action_id)` default 는 `action_<id>` 호출.
    화면별 특수 처리 (wizard / 별명 매핑) 는 override.

    `_menubar_widget_id()` default = None → 첫 번째 MenuBar 위젯 사용.
    같은 화면에 MenuBar 가 여러 개일 때만 override.
    """

    # mixin 이 worker 를 kick — 자식이 self._open_menu_loop 를 호출.
    def _build_menus(self) -> tuple[MenuSpec, ...]:
        """자식이 override. default = 빈 tuple → 메뉴 없음 (mixin 무력화)."""
        return ()

    def _menubar_widget_id(self) -> str | None:
        """자식이 override 가능 — MenuBar 의 id 가 다른 경우 (충돌 회피).
        default None = 첫 번째 MenuBar 위젯을 사용 (단일 컴포지션 가정).
        """
        return None

    def _dispatch_menu_action(self, action_id: str) -> None:
        """default — `action_<id>` 메서드 호출. 자식이 override 로 wizard 등
        특수 처리 추가 가능 — super()._dispatch_menu_action(action_id) 로
        fallback.
        """
        method = getattr(self, f"action_{action_id}", None)
        if callable(method):
            method()
        else:
            # status bar 가 있으면 안내, 없으면 silent (test friendly).
            log.debug("메뉴 dispatch — action 없음: %s", action_id)

    # ---- F10 진입 + popup loop -------------------------------------------

    def action_open_menu(self) -> None:
        """F10 / Alt / Alt+M / Alt+F — sync wrapper. 실제 popup loop 는
        worker 안 (push_screen_wait 가 worker context 필요).
        """
        # `@work` 데코된 메서드는 sync 호출이 worker 를 spawn — `self` binding 유지.
        self._menu_loop_worker(start_index=0)

    def on_menu_bar_menu_clicked(self, event: "MenuBar.MenuClicked") -> None:
        """CL #52759+: 마우스 클릭으로 메뉴 진입 — 클릭한 메뉴에서 popup 시작."""
        self._menu_loop_worker(start_index=event.menu_index)
        event.stop()

    # `@work` 는 textual.work.work — runtime import.
    def _menu_loop_worker(self, start_index: int = 0) -> None:
        # 기본 호출자는 `action_open_menu` 의 wrapper. 진짜 worker 는 아래
        # _MenuOpenLoop 에서 spawn — 자식 클래스가 별도로 override 하지
        # 않아도 동작하도록 본 mixin 안에서 wiring.
        from textual import work as _twork
        # textual.work decorator 는 메서드 호출 시 worker 를 만듦. 본 함수는
        # decorator 가 아닌 wrapper 라 직접 worker 를 만든다.
        if hasattr(self, "run_worker"):
            self.run_worker(  # type: ignore[attr-defined]
                self._open_menu_loop_async(start_index),
                exclusive=True, group="menubar", name="open_menu",
            )
        else:  # pragma: no cover — non-Screen mixin 사용 시
            import asyncio
            asyncio.create_task(self._open_menu_loop_async(start_index))

    async def _open_menu_loop_async(self, start_index: int = 0) -> None:
        """popup 순환 — ←/→ 로 다른 메뉴, Enter 로 dispatch, Esc 취소."""
        menus = self._build_menus()
        if not menus:
            return
        bar: MenuBar | None = None
        try:
            wid = self._menubar_widget_id()
            if wid is not None:
                bar = self.query_one(f"#{wid}", MenuBar)  # type: ignore[attr-defined]
            else:
                bar = self.query_one(MenuBar)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover — MenuBar 없는 화면은 popup 만.
            bar = None

        idx = max(0, min(start_index, len(menus) - 1))
        while True:
            if bar is not None:
                bar.set_active(idx)
            offset = menubar_left_offset_for(idx, menus)
            result = await self.app.push_screen_wait(  # type: ignore[attr-defined]
                MenuPopup(menus[idx], margin_left=offset),
            )
            if result is None:
                return
            if isinstance(result, tuple) and result[:1] == ("nav",):
                direction = result[1]
                if direction == "left":
                    idx = (idx - 1) % len(menus)
                elif direction == "right":
                    idx = (idx + 1) % len(menus)
                continue
            if isinstance(result, str):
                self._dispatch_menu_action(result)
            return


# log import 해 두기 — 위에서 사용 (MenuBarMixin._dispatch_menu_action).
import logging as _logging  # noqa: E402  (mixin 정의 이후 inject)
log = _logging.getLogger(__name__)
