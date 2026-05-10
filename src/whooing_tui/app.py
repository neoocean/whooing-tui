"""Textual App 골격 — Phase 1.

현재는 자리만 잡아두고 (`run_app()` 호출 시 정보 메시지만 출력) Phase 2 의
실제 화면 구현(sections / accounts / entries) 을 기다린다. CLI 헤드리스
경로(`python -m whooing_tui sections list`) 는 이 모듈 없이도 동작한다.

Phase 2 구현 예정:
  - HomeScreen: 섹션 picker → entries 목록
  - EntriesScreen: DataTable 로 거래내역, 검색·필터·페이지네이션
  - 입력 dialog: 거래 추가/수정 (자주입력·매월입력 자동 매칭)
"""

from __future__ import annotations

import logging
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Static

from whooing_tui import __version__
from whooing_tui.config import load_config

log = logging.getLogger(__name__)


_CSS_PATH = Path(__file__).resolve().parent / "theming.tcss"


_PLACEHOLDER_BODY = """\
[bold]whooing-tui[/bold] v{version}

Phase 1 골격이 정상으로 켜졌습니다. Phase 2 에서 실제 화면이 들어갑니다.

지금 가능한 헤드리스 명령:
  • [cyan]whooing-tui sections list[/cyan]
  • [cyan]whooing-tui accounts list[/cyan]
  • [cyan]whooing-tui entries list --days 30[/cyan]

종료: [yellow]q[/yellow] 또는 [yellow]Ctrl+C[/yellow]
"""


class WhooingTuiApp(App):
    """Phase 1 자리표시자 앱. Phase 2 에서 HomeScreen 으로 대체."""

    CSS_PATH = str(_CSS_PATH) if _CSS_PATH.exists() else None
    TITLE = "whooing-tui"
    SUB_TITLE = "Whooing 가계부 — Textual TUI"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("t", "toggle_theme", "Theme", show=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        body = Static(
            _PLACEHOLDER_BODY.format(version=__version__),
            id="placeholder",
        )
        yield Container(body, id="main")
        yield Footer()

    def on_mount(self) -> None:
        cfg = load_config()
        try:
            self.theme = cfg.theme
        except Exception:
            log.debug("테마 적용 실패 (config.theme=%r) — 무시", cfg.theme)

    def action_toggle_theme(self) -> None:
        try:
            current = getattr(self, "theme", "textual-dark")
            self.theme = (
                "textual-light" if current.endswith("dark") else "textual-dark"
            )
        except Exception:
            pass


def run_app() -> int:
    """TUI 실행 진입점. 정상 종료 시 0."""
    app = WhooingTuiApp()
    app.run()
    return 0
