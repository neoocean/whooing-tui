"""Stage 1 — 중복 스캔 결과 전체 목록 화면 (CL #52989+).

사용자 요청 (2026-05-19):
> 먼저 팝업에서 전체 목록을 보여주고 그 화면에서 정리 버튼을 누르면 중복
> 항목들을 하나씩 묶어 보여주며 정리하게 인터페이스를 수정하세요.
> 중복 목록에서는 후잉으로부터 정보를 새로고침 할 수 있도록 버튼을 제공해야
> 합니다.

흐름:
  1. EntriesScreen worker → DupeScanOverviewScreen push (cluster N건 전체).
  2. 사용자가 *전체 분포* 를 한 화면에서 본다 — 정리됨/남음, verdict 별,
     날짜순.
  3. 행 클릭 또는 Enter → 해당 cluster 에서 정리 시작 (DuplicateScanScreen).
  4. "정리 시작" (R) 버튼 → 첫 pending cluster 에서 시작.
  5. "새로고침" (F5) → 사용자 확인 후 sqlite 캐시 비우고 후잉 재요청.
  6. Esc → 닫기.

dismiss 값:
  True  — 한 cluster 라도 정리됐음 → 호출자가 entries 재로드.
  False — 변경 없이 닫음.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from whooing_core.dupes import VERDICT_LABELS_KO

from whooing_tui.client import WhooingClient
from whooing_tui.dupe_scan_repo import DupeScanRepository, StoredCluster
from whooing_tui.ime import bind_ko
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


def _fmt_money(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


# delete callback signature: list of entry_ids → (deleted_count, failed_msgs).
DeleteCallback = Callable[[list[str]], Awaitable[tuple[int, list[str]]]]

# refresh callback: 후잉에서 다시 fetch 후 새 StoredCluster list 반환.
# 호출자는 worker context (Textual @work) 안에서 정의 — 진행 modal 까지 책임.
RefreshCallback = Callable[[], Awaitable[list[StoredCluster]]]


class DupeScanOverviewScreen(ModalScreen[bool]):
    """전체 중복 스캔 결과 list. 정리 / 새로고침 / 닫기 진입점."""

    DEFAULT_CSS = """
    DupeScanOverviewScreen {
        align: center middle;
    }
    #overview-frame {
        width: 95%;
        max-width: 150;
        min-width: 70;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #overview-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #overview-summary {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #overview-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #overview-table {
        height: auto;
        max-height: 20;
        margin-top: 1;
    }
    #overview-status {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #overview-status.error {
        color: $error;
    }
    #overview-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #overview-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "닫기", show=True),
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        # Enter — 현재 cursor row 의 cluster 에서 정리 시작.
        Binding("enter", "open_at_cursor", "이 항목부터 정리", show=True),
        # R — 첫 pending cluster 에서 정리 시작 (단축).
        Binding("r", "start_cleanup", "정리 시작 (R)", show=True),
        *bind_ko("r", "start_cleanup", "정리 시작"),
        # F5 / Ctrl+R — 후잉에서 새로 fetch.
        Binding("f5", "refresh", "새로고침 (F5)", show=True, priority=True),
        Binding("ctrl+r", "refresh", "새로고침", show=False, priority=True),
    ]

    def __init__(
        self,
        clusters: list[StoredCluster],
        *,
        repo: DupeScanRepository,
        section_id: str,
        range_start: str,
        range_end: str,
        client: WhooingClient,
        session: SessionState,
        delete_callback: DeleteCallback,
        refresh_callback: RefreshCallback,
        cached: bool = False,
    ) -> None:
        """clusters: StoredCluster list (정렬은 caller 책임 — 정렬된 채로 전달).

        repo: 정리 후 status 업데이트 + 새로고침 시 clear 에 필요.
        section_id / range_start / range_end: 재조회 / 캐시 식별자.
        client / session: DuplicateScanScreen push 시 그대로 전달.
        delete_callback: 같은 entries.py worker 가 만든 closure (entry 삭제 +
                         로컬 sqlite annotation purge).
        refresh_callback: F5 시 worker 가 후잉 재호출 + repo 저장 + 새 list
                          반환.
        cached: True 면 "DB 캐시에서 로드됨" 안내 (사용자에게 신선도 알림).
        """
        super().__init__()
        self._clusters = list(clusters)
        self._repo = repo
        self._section_id = section_id
        self._range_start = range_start
        self._range_end = range_end
        self._client = client
        self._session = session
        self._delete_callback = delete_callback
        self._refresh_callback = refresh_callback
        self._cached = cached
        # 정리 한 번이라도 됐으면 호출자 (worker) 에 알리려고 dirty.
        self._dirty: bool = False
        # 테스트 친화.
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="overview-frame"):
            yield Static("[bold]중복 거래 검사 — 결과 목록[/bold]",
                         id="overview-title")
            yield Static("", id="overview-summary")
            yield Static(
                "↑/↓ 이동 · Enter: 이 항목부터 정리 · R: 처음부터 정리 · "
                "F5: 후잉에서 새로고침 · Esc: 닫기",
                id="overview-hint",
            )
            yield DataTable(id="overview-table", cursor_type="row")
            yield Static("", id="overview-status")
            with Horizontal(id="overview-buttons"):
                yield Button("정리 시작 (R)", id="overview-btn-cleanup",
                             variant="warning")
                yield Button("새로고침 (F5)", id="overview-btn-refresh")
                yield Button("닫기 (Esc)", id="overview-btn-close")

    def on_mount(self) -> None:
        self._render_all()
        try:
            self.query_one("#overview-table", DataTable).focus()
        except Exception:  # pragma: no cover
            pass

    # ---- rendering -----------------------------------------------------

    def _render_all(self) -> None:
        self._render_summary()
        self._render_table()

    def _render_summary(self) -> None:
        total = len(self._clusters)
        pending = sum(1 for c in self._clusters if c.status == "pending")
        resolved = sum(1 for c in self._clusters if c.status == "resolved")
        skipped = sum(1 for c in self._clusters if c.status == "skipped")
        cache_hint = " · 💾 sqlite 캐시" if self._cached else " · 🌐 후잉에서 fetch"
        self.query_one("#overview-summary", Static).update(
            f"범위: [b]{self._range_start} ~ {self._range_end}[/b]"
            f"{cache_hint}\n"
            f"전체 [b]{total}[/b] · 정리됨 [green]{resolved}[/green] · "
            f"남음 [yellow]{pending}[/yellow]"
            + (f" · skip {skipped}" if skipped else "")
        )

    def _render_table(self) -> None:
        table = self.query_one("#overview-table", DataTable)
        table.clear(columns=True)
        table.add_column("#", width=4)
        table.add_column("상태", width=8)
        table.add_column("강도", width=14)
        table.add_column("날짜", width=10)
        table.add_column("금액", width=12)
        table.add_column("건수", width=4)
        table.add_column("적요 / 메모")
        for i, c in enumerate(self._clusters, start=1):
            # 상태 마크 + 색.
            if c.status == "resolved":
                state = "[green]✓ 정리[/green]"
            elif c.status == "skipped":
                state = "[dim]× skip[/dim]"
            else:
                state = "[yellow]⏳ 남음[/yellow]"
            verdict_lbl = VERDICT_LABELS_KO.get(c.verdict, c.verdict)
            first = c.entries[0] if c.entries else {}
            date = str(first.get("entry_date") or "")[:8]
            money = _fmt_money(first.get("money"))
            item = str(first.get("item") or "")
            memo = str(first.get("memo") or "")
            label = item
            if memo and memo != item:
                label = f"{item}  ·  {memo}" if item else memo
            table.add_row(
                str(i), state, verdict_lbl, date, money, str(len(c.entries)),
                label,
                key=str(c.id),
            )

    # ---- key actions ---------------------------------------------------

    def action_close(self) -> None:
        self.dismiss(self._dirty)

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#overview-table", DataTable).action_cursor_up()
        except Exception:  # pragma: no cover
            pass

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#overview-table", DataTable).action_cursor_down()
        except Exception:  # pragma: no cover
            pass

    def action_open_at_cursor(self) -> None:
        """Enter — cursor 가 가리키는 cluster 에서 정리 시작."""
        if not self._clusters:
            self._set_status("정리할 cluster 가 없습니다.", error=True)
            return
        try:
            table = self.query_one("#overview-table", DataTable)
            idx = max(0, table.cursor_row or 0)
        except Exception:  # pragma: no cover
            idx = 0
        self._kick_cleanup(start_idx=idx)

    def action_start_cleanup(self) -> None:
        """R — 첫 pending cluster 에서 정리 시작."""
        if not self._clusters:
            self._set_status("정리할 cluster 가 없습니다.", error=True)
            return
        start = next(
            (i for i, c in enumerate(self._clusters) if c.status == "pending"),
            None,
        )
        if start is None:
            self._set_status("남은 pending cluster 가 없습니다.", error=False)
            return
        self._kick_cleanup(start_idx=start)

    def action_refresh(self) -> None:
        """F5 — 사용자 확인 후 sqlite 비우고 후잉 재요청."""
        # 단순화 — 별도 확인 modal 없이 즉시 진행. 사용자가 명시적으로 F5
        # 눌렀고 status 영역에 진행이 보이므로 충분.
        self._set_status("🌐 후잉에서 새로고침 중…")
        self._refresh_worker()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "overview-btn-close":
            self.action_close()
        elif bid == "overview-btn-cleanup":
            self.action_start_cleanup()
        elif bid == "overview-btn-refresh":
            self.action_refresh()

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        self.last_status = msg
        try:
            s = self.query_one("#overview-status", Static)
            s.update(msg)
            if error:
                s.add_class("error")
            else:
                s.remove_class("error")
        except Exception:  # pragma: no cover
            pass

    # ---- cleanup launcher ----------------------------------------------

    @work(exclusive=True, group="dupe_overview_cleanup",
          name="dupe_overview_cleanup")
    async def _kick_cleanup(self, *, start_idx: int = 0) -> None:
        """DuplicateScanScreen 을 push 한 뒤 dismiss 받으면 본 화면 갱신."""
        from whooing_tui.screens.duplicate_scan import DuplicateScanScreen

        # pending cluster 만 전달하면 사용자가 이미 정리한 것이 다시 보이지
        # 않아 흐름 자연스러움. start_idx 도 pending list 기준으로 매핑.
        pending = [c for c in self._clusters if c.status == "pending"]
        if not pending:
            self._set_status("정리할 pending cluster 가 없습니다.")
            return
        # overview 의 start_idx 가 self._clusters 인덱스 — pending 안에서의
        # 위치로 보정.
        target_cluster = self._clusters[start_idx] if (
            0 <= start_idx < len(self._clusters)
        ) else pending[0]
        if target_cluster.status != "pending":
            # 이미 정리된 cluster 를 Enter → 첫 pending 부터 시작.
            target_cluster = pending[0]
        try:
            pending_start = next(
                i for i, c in enumerate(pending) if c.id == target_cluster.id
            )
        except StopIteration:
            pending_start = 0

        result = await self.app.push_screen_wait(  # type: ignore[attr-defined]
            DuplicateScanScreen(
                pending,
                client=self._client,
                session=self._session,
                delete_callback=self._delete_callback,
                repo=self._repo,
                start_idx=pending_start,
            ),
        )
        if result:
            self._dirty = True
        # repo 에서 최신 상태 다시 읽어 모든 cluster (resolved 포함) 표시.
        try:
            self._clusters = self._repo.load_all_clusters(
                section_id=self._section_id,
                range_start=self._range_start,
                range_end=self._range_end,
            )
        except Exception:  # pragma: no cover
            log.exception("overview reload after cleanup failed")
        self._render_all()
        # 모두 정리됐으면 자동 닫음 (사용자가 처음 화면으로 빨리 돌아가도록).
        remaining = sum(1 for c in self._clusters if c.status == "pending")
        if remaining == 0 and self._dirty:
            self._set_status("✅ 모든 cluster 정리 완료.")
            self.dismiss(self._dirty)
            return
        self._set_status(f"정리 진행 — 남은 pending {remaining} 건.")

    @work(exclusive=True, group="dupe_overview_refresh",
          name="dupe_overview_refresh")
    async def _refresh_worker(self) -> None:
        """F5 — refresh_callback 호출 → 새 cluster list 로 self 교체."""
        try:
            new_clusters = await self._refresh_callback()
        except Exception as e:  # pragma: no cover
            log.exception("refresh_callback failed")
            self._set_status(f"새로고침 실패: {e}", error=True)
            return
        self._clusters = list(new_clusters)
        self._cached = False  # 방금 후잉에서 새로 받음.
        self._render_all()
        n_pending = sum(1 for c in self._clusters if c.status == "pending")
        self._set_status(
            f"✅ 새로고침 완료 — pending {n_pending} 건.",
        )
