"""HelpModal — 현재 화면의 키 바인딩을 한눈에 보여주는 ModalScreen.

각 화면 (HomeScreen / EntriesScreen) 의 `?` 키가 본 modal 을 push.
modal 은 호출 시점의 Screen 인스턴스의 `BINDINGS` 와 화면 이름을 받아
표 형태로 노출한다 — 화면이 새 단축키를 추가해도 도움말이 자동 동기화.

Esc / `?` / `q` 어느 쪽으로도 닫힌다.
"""

from __future__ import annotations

from typing import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from whooing_tui.ime import bind_ko


def _format_bindings(bindings: Iterable[Binding]) -> str:
    """BINDINGS list 를 사람이 읽기 좋은 정렬 표로.

    숨김 (`show=False`) 바인딩은 제외 — 사용자가 의도적으로 'visible'
    이라고 표기한 것만. priority 여부는 표시 안 함 (사용자에겐 의미 없음).
    """
    # textual 8 의 Binding 은 `key`, `description`, `show`, `key_display` 를
    # 가진다. 일부는 동일 액션에 여러 키 (예: q, ctrl+c) — 같은 description
    # 으로 묶어준다.
    visible: list[tuple[str, str]] = []
    for b in bindings:
        if not getattr(b, "show", True):
            continue
        desc = (b.description or "").strip()
        if not desc:
            continue
        key = b.key_display or b.key
        visible.append((str(key), desc))
    if not visible:
        return "  (이 화면에는 보이는 단축키가 없습니다)"
    # 같은 description 끼리 묶기
    by_desc: dict[str, list[str]] = {}
    for key, desc in visible:
        by_desc.setdefault(desc, []).append(key)
    lines: list[str] = []
    width = max(len(" / ".join(keys)) for keys in by_desc.values())
    for desc, keys in by_desc.items():
        keys_str = " / ".join(keys)
        lines.append(f"  [bold]{keys_str:<{width}}[/bold]   {desc}")
    return "\n".join(lines)


class HelpModal(ModalScreen[None]):
    """현재 활성 Screen 의 BINDINGS 를 한 표로 보여주는 모달."""

    DEFAULT_CSS = """
    HelpModal {
        align: center middle;
    }
    #help-frame {
        /* CL #51120+: 좁은 터미널 대응. */
        width: 95%;
        max-width: 64;
        min-width: 30;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #help-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #help-body {
        padding: 1 0;
        height: auto;
    }
    #help-foot {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("question_mark", "close", "Close", show=False),
        *bind_ko("q", "close", "Close", show=False),
    ]

    def __init__(self, screen_title: str, bindings: list[Binding]) -> None:
        super().__init__()
        self._screen_title = screen_title
        # CL #52816+: 인자 이름은 `bindings` 그대로지만 attribute 는
        # `_help_bindings` 로 보관 — Textual 의 `Screen._bindings`
        # (BindingsMap 인스턴스) 와 충돌 회피. 충돌 시 `Esc` 누르면
        # `'list' object has no attribute 'key_to_bindings'` AttributeError.
        self._help_bindings = bindings
        # 테스트가 Static 의 사적 API (`renderable`) 에 의존하지 않도록
        # 평문 본문을 attribute 로 보관 (HomeScreen.last_status 와 동일 컨벤션).
        self.body_text: str = _format_bindings(bindings)

    def compose(self) -> ComposeResult:
        with Vertical(id="help-frame"):
            yield Static(f"[bold]{self._screen_title} — 키 바인딩[/bold]", id="help-title")
            yield Static(self.body_text, id="help-body")
            yield Static("Esc / ? / q : 닫기", id="help-foot")

    def action_close(self) -> None:
        self.dismiss(None)
