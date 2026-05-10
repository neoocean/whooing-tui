"""Textual App — Phase 2a.

`run_app()` 이 진입점. 토큰을 .env / 환경변수에서 로드해 WhooingClient 를
만들고 HomeScreen 을 push 한다. 토큰 누락 / placeholder 인 경우 GUI 를
띄우지 않고 stderr 로 안내한 뒤 비-0 종료 — TUI 안에서의 에러 모달보다
사용자가 즉시 고치기 쉽다.

Phase 2 후속:
  - EntriesScreen (HomeScreen 에서 enter 두 번에 push)
  - EntryEditDialog (거래 추가/수정)
  - WhooingClient 에 POST/PUT/DELETE
  - 로컬 sqlite 캐시
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from whooing_tui import __version__
from whooing_tui.auth import load_auth_from_env
from whooing_tui.cache import CacheStore, default_cache_path
from whooing_tui.client import CachedWhooingClient, WhooingClient
from whooing_tui.config import load_config
from whooing_tui.screens.home import HomeScreen
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


_CSS_PATH = Path(__file__).resolve().parent / "theming.tcss"


class WhooingTuiApp(App):
    """Whooing TUI 메인 앱.

    - 단일 SessionState (`self.session`) 가 활성 섹션과 계정 캐시를 보관.
    - WhooingClient 는 생성자에서 주입 (테스트는 fake client 로 대체 가능).
    """

    CSS_PATH = str(_CSS_PATH) if _CSS_PATH.exists() else None
    TITLE = "whooing-tui"
    SUB_TITLE = f"Whooing 가계부 — v{__version__}"

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("t", "toggle_theme", "Theme", show=True),
    ]

    def __init__(self, client: Optional[WhooingClient] = None) -> None:
        super().__init__()
        self._client = client
        self.session = SessionState()

    def compose(self) -> ComposeResult:
        # HomeScreen 이 자체 Header/Footer 를 가지므로 root 는 비워둔다.
        # 단, App.run_test() 환경 등에서 HomeScreen push 전에 잠시
        # 보일 수 있어 minimal Header/Footer 를 제공.
        yield Header(show_clock=False)
        yield Footer()

    def on_mount(self) -> None:
        cfg = load_config()
        try:
            self.theme = cfg.theme
        except Exception:
            log.debug("테마 적용 실패 (config.theme=%r) — 무시", cfg.theme)

        if self._client is None:
            # 정상 부팅 경로에서는 run_app() 이 client 를 주입한다.
            # client 가 None 이면 테스트가 직접 만든 경우이므로 화면도
            # 띄우지 않는다 (테스트는 fake client 를 넘긴다).
            return
        self.push_screen(HomeScreen(self._client))

    def action_toggle_theme(self) -> None:
        try:
            current = getattr(self, "theme", "textual-dark")
            self.theme = (
                "textual-light" if current.endswith("dark") else "textual-dark"
            )
        except Exception:
            pass


def run_app() -> int:
    """TUI 실행 진입점. 정상 종료 시 0, 토큰 문제 시 3 (AUTH 와 동일).

    config.cache.enabled 가 true (기본) 면 sqlite 캐시 wrapper 를 두른다 —
    accounts/entries 의 inter-session 캐시로 후잉 한도 부담을 줄인다.
    """
    try:
        auth = load_auth_from_env()
    except ValueError as e:
        # GUI 띄우기 전에 stderr 로 안내 — 사용자가 즉시 .env 를 고치게.
        print(f"error [USER_INPUT] {e}", file=sys.stderr)
        return 3
    cfg = load_config()
    raw_client = WhooingClient(auth)
    if cfg.cache_enabled:
        project_root = Path(__file__).resolve().parents[2]
        store = CacheStore(default_cache_path(project_root))
        client = CachedWhooingClient(
            raw_client, store,
            accounts_ttl_sec=cfg.cache_accounts_ttl_sec,
            entries_ttl_sec=cfg.cache_entries_ttl_sec,
        )
    else:
        client = raw_client  # type: ignore[assignment]
    app = WhooingTuiApp(client=client)
    app.run()
    return 0
