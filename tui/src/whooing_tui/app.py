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

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Static

from whooing_tui import __version__
from whooing_tui.auth import load_auth_from_env
from whooing_tui.cache import CacheStore, default_cache_path
from whooing_tui.client import CachedWhooingClient, WhooingClient
from whooing_tui.config import load_config
from whooing_tui.ime import bind_ko
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


_CSS_PATH = Path(__file__).resolve().parent / "theming.tcss"


class _ShutdownModal(ModalScreen[None]):
    """CL #52761+ (CL #52819+ live commands list): 종료 중 진행 모달.

    q 키 누른 직후 push — P4 flush + 진행 중 worker 들이 끝날 때까지 표시.
    종전엔 q → 즉시 exit → unmount 단계의 `flush_on_exit` 동안 cli 가 응답
    없음 상태. 이제 TUI 안에서 모달로 진행, 끝나면 자동 exit.

    CL #52819+ 사용자 요청:
    - 종료 중 *현재 실행 중인 커맨드* 를 함께 표시 (Textual worker + p4
      submit thread). 250ms 마다 갱신.
    - 종료 시퀀스는 **취소 불가** — Esc / q / ctrl+c 모두 무시. (사용자
      의도된 ctrl+c 강제 종료 path 는 App 의 ctrl+c → action_quit 이 별도
      처리하지만 본 모달 위에서는 모달의 binding 이 우선이라 silent.)
    - 끝나면 즉시 exit → CLI 프롬프트 즉시 반환 (p4 thread 들이 join 되어
      non-daemon 이 남아있지 않게).
    """

    # 명시 BINDINGS — Esc / q / ctrl+c 모두 noop (사용자 요청: 취소 불가).
    BINDINGS = [
        Binding("escape", "noop", "", show=False, priority=True),
        Binding("q", "noop", "", show=False, priority=True),
        Binding("ctrl+c", "noop", "", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    _ShutdownModal {
        align: center middle;
    }
    #shutdown_box {
        width: 95%;
        max-width: 70;
        min-width: 40;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #shutdown_title {
        height: 1;
        content-align: center middle;
        color: $primary;
    }
    #shutdown_intro {
        padding: 1 0 0 0;
        height: auto;
        color: $text-muted;
    }
    #shutdown_tasks {
        padding: 1 0;
        height: auto;
    }
    #shutdown_foot {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="shutdown_box"):
            yield Static("[bold]종료 중…[/bold]", id="shutdown_title")
            yield Static(
                "남은 작업이 끝나면 자동으로 종료합니다.",
                id="shutdown_intro",
            )
            yield Static("(작업 목록 수집 중…)", id="shutdown_tasks")
            yield Static(
                "[dim]종료 시퀀스는 취소할 수 없습니다.[/dim]",
                id="shutdown_foot",
            )

    def on_mount(self) -> None:
        """250ms 마다 작업 목록 refresh — Textual worker + p4 thread."""
        # 테스트가 검사할 수 있도록 마지막 라벨들을 attribute 로 노출.
        self.last_task_labels: list[str] = []
        self._refresh_tasks()
        self.set_interval(0.25, self._refresh_tasks)

    def action_noop(self) -> None:
        """취소 시도 무시 — 사용자 요청: 종료 시퀀스 시작되면 취소 불가."""
        return

    def _refresh_tasks(self) -> None:
        """현재 실행 중인 commands 를 모아 `#shutdown_tasks` 갱신."""
        lines: list[str] = []

        # 1) Textual workers — RUNNING 상태만. 본 모달의 shutdown_flush 는
        #    제외 (자기 자신 표시는 사용자에게 무의미).
        try:
            from textual.worker import WorkerState
            for w in list(self.app.workers):
                if w.state != WorkerState.RUNNING:
                    continue
                if w.name == "shutdown_flush":
                    continue
                label = w.name or (w.group or "worker")
                if w.group and w.group != w.name:
                    label = f"{w.group}/{label}"
                lines.append(f"  • {label}")
        except Exception:  # pragma: no cover — worker API 변경 보호
            log.debug("worker enumeration failed", exc_info=True)

        # 2) P4 submit threads — pending_count.
        try:
            from whooing_tui import p4_sync
            n_p4 = p4_sync.pending_count()
            if n_p4 > 0:
                lines.append(f"  • P4 submit {n_p4}건 대기 중")
        except Exception:  # pragma: no cover
            log.debug("p4 pending count failed", exc_info=True)

        # 테스트 친화 — 마지막 라벨 list 노출.
        self.last_task_labels = [s.strip("• ").strip() for s in lines]

        try:
            tasks = self.query_one("#shutdown_tasks", Static)
            if lines:
                tasks.update("실행 중:\n" + "\n".join(lines))
            else:
                tasks.update("[dim]마지막 정리 중…[/dim]")
        except Exception:  # pragma: no cover
            pass


class _StartupCheckScreen(ModalScreen[bool]):
    """CL #52832+ 사용자 요청: 앱 시작 시 P4 db 상태 확인 splash 모달.

    절차:
      1. 모달 표시 즉시 "데이터베이스 확인 중…" → 사용자가 무엇이 진행 중인지
         즉시 알도록.
      2. 로컬에 unsubmitted 변경 있으면 우선 *blocking* 으로 submit.
         (다른 환경의 다음 시작이 head 를 sync 받을 수 있도록.)
      3. P4 head 보다 오래된 (sync 필요) 상태인지 확인.
         - 오래됨 → 에러 메시지 + 닫기 버튼 → dismiss(False) → 앱 종료.
         - 최신 → dismiss(True) → app 이 EntriesScreen push.

    `WHOOING_DATA_DIR` 가 명시 set 되면 (테스트 격리) 모든 검사 skip — 즉시
    dismiss(True). P4 환경 부재 / db 가 workspace 매핑 외인 경우도 silent skip.

    종료 시퀀스처럼 *취소 불가* — Esc / q / ctrl+c noop.
    """

    BINDINGS = [
        Binding("escape", "noop", "", show=False, priority=True),
        Binding("q", "noop", "", show=False, priority=True),
        Binding("ctrl+c", "noop", "", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    _StartupCheckScreen {
        align: center middle;
    }
    #startup_box {
        width: 95%;
        max-width: 70;
        min-width: 40;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }
    #startup_title {
        height: 1;
        content-align: center middle;
        color: $primary;
    }
    #startup_status {
        padding: 1 0;
        height: auto;
    }
    #startup_status.error {
        color: $error;
    }
    #startup_buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
        display: none;
    }
    #startup_buttons.visible {
        display: block;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        # 테스트 친화: 진행 단계 / 결과를 attribute 로 노출.
        self.stage: str = "init"  # init|checking|submitting|outdated|ok|skipped
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        from textual.widgets import Button
        with Vertical(id="startup_box"):
            yield Static("[bold]whooing-tui 시작 중…[/bold]", id="startup_title")
            yield Static(
                "데이터베이스 상태를 확인합니다…",
                id="startup_status",
            )
            with Horizontal(id="startup_buttons"):
                yield Button("닫기", id="startup_btn_close", variant="error")

    def on_mount(self) -> None:
        self._set_status("데이터베이스 상태를 확인합니다…")
        self._run_check()

    def action_noop(self) -> None:
        """검사 진행 중 cancel 불가."""
        return

    def on_button_pressed(self, event) -> None:
        if event.button.id == "startup_btn_close":
            # outdated 상태 — dismiss(False) → app 종료.
            self.dismiss(False)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.last_status = text
        try:
            s = self.query_one("#startup_status", Static)
            s.update(text)
            if error:
                s.add_class("error")
            else:
                s.remove_class("error")
        except Exception:  # pragma: no cover
            pass

    def _show_close_button(self) -> None:
        try:
            self.query_one("#startup_buttons").add_class("visible")
            from textual.widgets import Button
            self.query_one("#startup_btn_close", Button).focus()
        except Exception:  # pragma: no cover
            pass

    @work(exclusive=True, group="startup", name="startup_check")
    async def _run_check(self) -> None:
        """blocking p4 작업은 thread executor 로 — main loop 차단 방지."""
        import asyncio
        import os

        # 테스트 격리: WHOOING_DATA_DIR set → 모두 skip.
        if os.getenv("WHOOING_DATA_DIR") is not None:
            self.stage = "skipped"
            self.dismiss(True)
            return

        try:
            from whooing_tui import data as tui_data
            from whooing_tui import p4_sync
            db_path = tui_data.db_path()
        except Exception:
            log.exception("startup check: db_path lookup failed")
            self.dismiss(True)  # 최선 노력 — 표면화 X.
            return

        loop = asyncio.get_running_loop()

        # 1) 로컬 unsubmitted 변경 → submit (blocking).
        self.stage = "checking"
        try:
            has_pending = await loop.run_in_executor(
                None, p4_sync.has_pending_local_changes, db_path,
            )
        except Exception:
            log.exception("startup: reconcile -n failed")
            has_pending = False

        if has_pending:
            self.stage = "submitting"
            self._set_status(
                "로컬 변경 사항이 있어 먼저 P4 에 submit 합니다…\n"
                "[dim]잠시만 기다려 주세요.[/dim]",
            )
            try:
                await loop.run_in_executor(
                    None, p4_sync.flush_on_exit, db_path,
                )
            except Exception:
                log.exception("startup: submit failed")
                # 실패해도 진행 — 다음 단계의 outdated 검사가 막을 수 있음.

        # 2) P4 head 와 비교 → outdated 면 종료.
        self._set_status("P4 head 와 비교 중…")
        try:
            outdated = await loop.run_in_executor(
                None, p4_sync.is_outdated_vs_p4, db_path,
            )
        except Exception:
            log.exception("startup: sync -n failed")
            outdated = False

        if outdated:
            self.stage = "outdated"
            self._set_status(
                "⚠️ 로컬 데이터베이스가 P4 head 보다 오래되었습니다.\n\n"
                "다른 환경에서 submit 된 변경 사항을 받지 못한 상태로 "
                "앱을 시작하면 충돌이 발생할 수 있습니다.\n\n"
                f"먼저 터미널에서 [bold]p4 sync {db_path}[/bold] 를 실행한 뒤 "
                "다시 시작하세요.",
                error=True,
            )
            self._show_close_button()
            return

        self.stage = "ok"
        self.dismiss(True)


class WhooingTuiApp(App):
    """Whooing TUI 메인 앱.

    - 단일 SessionState (`self.session`) 가 활성 섹션과 계정 캐시를 보관.
    - WhooingClient 는 생성자에서 주입 (테스트는 fake client 로 대체 가능).
    """

    CSS_PATH = str(_CSS_PATH) if _CSS_PATH.exists() else None
    TITLE = "whooing-tui"
    SUB_TITLE = f"Whooing 가계부 — v{__version__}"

    # CL #52720+: 단축키는 IME (한글 두벌식) 켜진 상태에서도 동작해야 한다.
    # `bind_ko` 가 영문 + 한글 자모 (`q`/`ㅂ`, `t`/`ㅅ`) 양쪽 binding 을 생성.
    # CL #52761+: q / ㅂ 의 action 을 `quit` 에서 `graceful_quit` 으로 —
    # 종료 모달을 띄우고 P4 flush 작업이 끝난 뒤 자동 exit. ctrl+c 는 종전
    # 그대로 즉시 exit (사용자가 강제 종료 의도).
    BINDINGS = [
        *bind_ko("q", "graceful_quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        *bind_ko("t", "toggle_theme", "Theme", show=True),
    ]

    def __init__(self, client: Optional[WhooingClient] = None) -> None:
        super().__init__()
        self._client = client
        self.session = SessionState()

    def compose(self) -> ComposeResult:
        # 초기 화면 (EntriesScreen) 이 자체 Header/Footer 를 가지므로 root
        # 는 비워둔다. App.run_test() 환경 등에서 push 전에 잠시 보일 수
        # 있어 minimal Header/Footer 를 제공.
        yield Header(show_clock=False)
        yield Footer()

    def on_mount(self) -> None:
        cfg = load_config()
        try:
            self.theme = cfg.theme
        except Exception:
            log.debug("테마 적용 실패 (config.theme=%r) — 무시", cfg.theme)

        # CL #52781+: 한글 자모 조합 활성화 — iOS Blink 같이 자모 단위로
        # 들어오는 환경에서 Input 의 value 를 음절로 합성.
        # `enable_hangul_composing` 은 idempotent — 두 번 호출해도 안전.
        try:
            from whooing_tui.widgets.hangul_input import enable_hangul_composing
            enable_hangul_composing()
        except Exception:  # pragma: no cover
            log.debug("한글 조합 활성화 실패 — 무시", exc_info=True)

        if self._client is None:
            # 정상 부팅 경로에서는 run_app() 이 client 를 주입한다.
            # client 가 None 이면 테스트가 직접 만든 경우이므로 화면도
            # 띄우지 않는다 (테스트는 fake client 를 넘긴다).
            return
        # CL #52832+ 사용자 요청: db 상태 확인 splash 를 먼저 — 로컬 변경
        # 이 있으면 submit, P4 head 보다 오래됐으면 종료. 검사 통과 (True)
        # 시 EntriesScreen push.
        self.push_screen(_StartupCheckScreen(), self._on_startup_check_done)

    def _on_startup_check_done(self, ok: bool | None) -> None:
        """`_StartupCheckScreen` 결과 처리.

        True  : 정상 → EntriesScreen 으로 진입.
        False : DB 가 P4 head 보다 오래됨 → 사용자가 sync 후 재시작해야.
                즉시 종료 (graceful_quit 거치지 않음 — 아직 변경 사항이
                없는 상태).
        None  : 모달이 비정상 dismiss (이론상 BINDINGS 가 cancel 차단).
                보수적으로 종료.
        """
        if ok and self._client is not None:
            self.push_screen(EntriesScreen(self._client))
            return
        self.exit()

    def on_unmount(self) -> None:
        """App 종료 직전 — 진행 중인 P4 sync submit 들을 끝까지 기다린 뒤,
        추가로 한 번 더 reconcile + submit (마지막 안전망).

        CL #51118+: 0.15.0~0.15.1 까지의 daemon thread 가 main thread 종료
        시 같이 죽어 마지막 mutation 의 자동 submit 이 미완료로 끝남.
        `wait_for_pending` 으로 모든 활성 submit join.
        CL #51119+ (사용자 요청): 추가로 종료 시점에 `flush_on_exit` 한 번
        더 — race / 누락 케이스에서도 마지막 변경이 P4 에 반영되도록.
        `WHOOING_DATA_DIR` env 이 명시 set 이면 (테스트 격리) skip.
        """
        try:
            import os
            from whooing_tui import data as tui_data
            from whooing_tui import p4_sync
            if os.getenv("WHOOING_DATA_DIR") is not None:
                # 테스트 / 명시 override — flush 자체가 사용자 실 db 를
                # 건드리지 않게 wait 만.
                p4_sync.wait_for_pending()
                return
            p4_sync.flush_on_exit(tui_data.db_path())
        except Exception:  # pragma: no cover — 종료 흐름은 절대 막지 않음
            log.debug("p4 sync flush failed at unmount", exc_info=True)

    def action_graceful_quit(self) -> None:
        """CL #52761+: q / ㅂ 종료 — 모달 표시 + flush 작업 → 자동 exit.

        흐름:
          1. `_ShutdownModal` push — 사용자에게 "종료 중" 안내 (즉시 보임).
          2. `_shutdown_worker` 가 thread executor 에서 `flush_on_exit`
             (blocking I/O 라 main loop 차단 방지).
          3. worker 완료 시 `self.exit()` → unmount → on_unmount 의 flush
             는 idempotent 두 번째 호출 (no-op).

        ctrl+c 는 종전 `action_quit` 으로 즉시 종료 (graceful 아님) — 사용자
        의도된 강제 종료 path.
        """
        # 이미 모달이 떠 있으면 (중복 q) noop.
        if isinstance(self.screen, _ShutdownModal):
            return
        self.push_screen(_ShutdownModal())
        self._shutdown_worker()

    @work(exclusive=True, group="shutdown", name="shutdown_flush")
    async def _shutdown_worker(self) -> None:
        """blocking flush 를 thread 로 보내고, 끝나면 exit."""
        import asyncio
        import os

        try:
            if os.getenv("WHOOING_DATA_DIR") is not None:
                # 테스트 격리 — wait_for_pending 만.
                from whooing_tui import p4_sync
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, p4_sync.wait_for_pending)
            else:
                from whooing_tui import data as tui_data
                from whooing_tui import p4_sync
                db_path = tui_data.db_path()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, p4_sync.flush_on_exit, db_path,
                )
        except Exception:  # pragma: no cover — 종료 흐름은 절대 막지 않음
            log.debug("graceful shutdown flush failed", exc_info=True)
        finally:
            self.exit()

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
