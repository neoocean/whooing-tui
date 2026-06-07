"""3년 거래 중복 일괄 스캔 화면 (CL #52963+).

사용자 요청 (2026-05-19):
> 거래내력에 중복으로 보이는 항목들이 많이 생겼습니다. 입력 풀다운메뉴
> 하위에 중복 거래 검사 메뉴를 넣고 지난 3년 동안의 거래를 검사해 중복인
> 항목들을 하나씩 보여주고 삭제할 것과 남길 것을 선택해 엔터를 누르면
> 중복이 처리되도록 해 주세요. 전체 중복 개수가 몇 개이며 지금이 몇 번째
> 중복인지도 보여주세요.

기존 `DuplicateEvalScreen` 은 *선택* 2건의 평가용 — 본 화면은 후잉 ledger
전체에서 cluster 들을 찾아 **한 cluster 씩 순회** 하며 dedup 진행.

흐름:
  1. EntriesScreen "입력" 메뉴 → "중복 거래 검사…" 클릭 →
     `action_scan_duplicates()` → worker 가 `list_entries(3년)` →
     `find_duplicate_clusters()` → 본 화면 push.
  2. 화면 헤더 — "중복 거래 검사  ·  N/T  (verdict)".
  3. DataTable 에 cluster 안 entry 들. ✓ 컬럼 = 삭제 대상 표시.
     기본값 — keep_suggestion 만 ✗ (보존), 나머지 ✓ (삭제 대상).
  4. Space — 현재 row 의 ✓/✗ toggle. 모두 ✓ (전부 삭제) 또는 모두 ✗
     (변화 없음) 도 허용 — 사용자 판단.
  5. Enter — 현재 cluster 처리 (✓ 가 표시된 거래 삭제, ✗ 만 남김),
     다음 cluster 로 자동 이동. 마지막이면 종료.
  6. n / → — skip (삭제 없이 다음 cluster).
  7. p / ← — 이전 cluster 로 되돌아가기 (이미 처리된 cluster 도 readonly
     로 표시, 진행 상황 추적용).
  8. Esc — 모든 진행 멈추고 닫기 (이미 삭제한 cluster 는 유지).

dismiss 값:
  - True   — 한 건이라도 삭제했음 → 호출자가 entries 재로드.
  - False  — 변화 없음 (Esc 또는 skip 만).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, OptionList, Static
from textual.widgets.option_list import Option

from whooing_core.dupes import (
    VERDICT_LABELS_KO,
    DupeCluster,
)

from whooing_tui.client import WhooingClient
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
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


class DupeScanRangeModal(ModalScreen[int | None]):
    """중복 검사 범위 선택 popup (CL #53006+).

    사용자 요청 (2026-05-19): "중복거래검사 범위를 1개월, 3개월 6개월
    1년 등으로 조절해 시작할 수 있도록 팝업에서 설정하게 해주세요."

    `dismiss` 값 = 검사할 일수 (int). 사용자가 Esc / 취소 누르면 None.
    호출자 (worker) 가 days 를 받아 `days_ago_yyyymmdd(days)` 로 start_date
    계산. 각 범위는 별도 sqlite cache slot 이므로 한 사용자가 여러 범위를
    번갈아 검사해도 상호 간섭 없음 (CL #52989+ 영구화 정책).
    """

    # (한글 라벨, 일수). 정렬 = 작은 범위 → 큰 범위. 3년이 default.
    OPTIONS: tuple[tuple[str, int], ...] = (
        ("1 개월", 30),
        ("3 개월", 90),
        ("6 개월", 180),
        ("1 년", 365),
        ("3 년 (기본)", 365 * 3),
        ("5 년", 365 * 5),
    )
    DEFAULT_DAYS: int = 365 * 3

    DEFAULT_CSS = """
    DupeScanRangeModal {
        align: center middle;
    }
    #range-frame {
        width: auto;
        min-width: 40;
        max-width: 60;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #range-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #range-prompt {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #range-list {
        height: auto;
        max-height: 10;
        margin-top: 1;
    }
    #range-hint {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소", show=True),
    ]

    def __init__(self, default_days: int | None = None) -> None:
        super().__init__()
        # 호출자가 마지막 선택값을 기억해 다음 진입 시 그 항목이 highlight
        # 되도록 — None 이면 클래스 DEFAULT_DAYS.
        self._initial_days: int = default_days or self.DEFAULT_DAYS
        # 테스트 친화.
        self.last_choice: int | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="range-frame"):
            yield Static(
                "[bold]🔍 중복 거래 검사 — 범위 선택[/bold]",
                id="range-title",
            )
            yield Static(
                "어느 기간의 거래를 검사할까요?", id="range-prompt",
            )
            yield OptionList(
                *[Option(label, id=str(days)) for label, days in self.OPTIONS],
                id="range-list",
            )
            yield Static(
                "↑/↓ 이동 · Enter: 시작 · Esc: 취소",
                id="range-hint",
            )

    def on_mount(self) -> None:
        try:
            ol = self.query_one("#range-list", OptionList)
            # default_days 와 일치하는 항목을 highlight. 일치 없으면 첫번째.
            try:
                idx = next(
                    i for i, (_, d) in enumerate(self.OPTIONS)
                    if d == self._initial_days
                )
            except StopIteration:
                idx = next(
                    i for i, (_, d) in enumerate(self.OPTIONS)
                    if d == self.DEFAULT_DAYS
                )
            ol.highlighted = idx
            ol.focus()
        except Exception:  # pragma: no cover
            pass

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        if event.option.id is None:
            return
        try:
            days = int(event.option.id)
        except (TypeError, ValueError):  # pragma: no cover
            self.dismiss(None)
            return
        self.last_choice = days
        self.dismiss(days)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ScanProgressModal(ModalScreen[None]):
    """중복 검사가 fetch/분석 중일 때 본 화면을 가리는 단순 진행 popup.

    CL #52977+ 사용자 요청: "중복 검사중일 때 화면을 작은 팝업으로 덮고
    중복 검사중이라고 안내해주세요. 그리고 지금 하고 있는 작업을 표시해
    주세요."

    worker 가 `app.push_screen(ScanProgressModal())` 으로 띄우고
    `set_activity(text)` 를 호출해 본문 갱신, 작업 완료 시 `dismiss()`.

    사용자 입력 없음 — BINDINGS 비어있어 Esc 도 무효. 작업이 끝나야 닫힘
    (긴 fetch 중 사용자가 멋대로 닫고 worker 만 계속 도는 상황 방지).
    실제로 화면을 가리고 있어 다른 액션도 막힘 → 일종의 modal busy 표시.
    """

    DEFAULT_CSS = """
    ScanProgressModal {
        align: center middle;
    }
    #scan-progress-frame {
        width: auto;
        min-width: 50;
        max-width: 70;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #scan-progress-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #scan-progress-activity {
        height: auto;
        min-height: 2;
        margin-top: 1;
        content-align: center middle;
    }
    #scan-progress-hint {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self, initial: str = "준비 중…") -> None:
        super().__init__()
        # mount 전에 set_activity 가 호출돼도 보존되도록 buffer.
        self._activity_text: str = initial
        # 테스트 친화.
        self.last_activity: str = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="scan-progress-frame"):
            yield Static(
                "[bold]🔍 중복 거래 검사 중[/bold]",
                id="scan-progress-title",
            )
            yield Static(self._activity_text, id="scan-progress-activity")
            yield Static(
                "잠시만 기다려주세요…",
                id="scan-progress-hint",
            )

    def set_activity(self, text: str) -> None:
        """현재 작업을 안내하는 텍스트 갱신 — worker 가 단계마다 호출.

        mount 이전에 호출돼도 안전 (buffer 에 저장, compose 가 사용).
        """
        self._activity_text = text
        self.last_activity = text
        try:
            self.query_one("#scan-progress-activity", Static).update(text)
        except Exception:  # pragma: no cover — not mounted yet.
            pass


class DuplicateScanScreen(ModalScreen[bool]):
    """전체 중복 cluster 를 하나씩 순회하며 dedup.

    DuplicateEvalScreen 과 달리 본 화면은:
      - cluster 안 *여러* 거래 동시 삭제 (✓ 표시) — keep 단일 radio 아님.
      - cluster 진행률 N/T 표시 (사용자가 위치 파악).
      - 한 화면 안에서 prev/next 이동 (작업 흐름 끊김 최소화).
    """

    DEFAULT_CSS = """
    DuplicateScanScreen {
        align: center middle;
    }
    #scan-frame {
        width: 95%;
        max-width: 140;
        min-width: 60;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #scan-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #scan-progress {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        text-style: bold;
    }
    #scan-progress.identical, #scan-progress.very_likely {
        color: $warning;
    }
    #scan-progress.possible {
        color: $accent;
    }
    #scan-reasons {
        height: auto;
        padding: 0 1;
        margin-top: 1;
        color: $text-muted;
    }
    #scan-hint {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #scan-table {
        height: auto;
        max-height: 14;
        margin-top: 1;
    }
    #scan-status {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #scan-status.error {
        color: $error;
    }
    #scan-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #scan-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "닫기", show=True),
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        # Space — 현재 row 의 삭제/보존 toggle.
        Binding("space", "toggle_delete", "삭제/보존 toggle",
                show=True, priority=True),
        # Enter — cluster 처리 + 다음 cluster.
        Binding("enter", "confirm", "확정 → 다음", show=True),
        # n / → — skip (삭제 없이 다음).
        Binding("n", "next_cluster", "skip →", show=True),
        Binding("right", "next_cluster", "skip", show=False),
        *bind_ko("n", "next_cluster", "skip"),
        # p / ← — 이전 cluster.
        Binding("p", "prev_cluster", "← 이전", show=True),
        Binding("left", "prev_cluster", "prev", show=False),
        *bind_ko("p", "prev_cluster", "prev"),
    ]

    def __init__(
        self,
        clusters: list,
        *,
        client: WhooingClient,
        session: SessionState,
        delete_callback: DeleteCallback | None = None,
        repo: Any = None,
        start_idx: int = 0,
    ) -> None:
        """clusters: DupeCluster 또는 StoredCluster list — 같은 shape (entries,
        verdict, reasons, keep_suggestion). StoredCluster 면 `.id` 와
        `.status` 가 있어 `repo` 와 함께 결과를 sqlite 에 영구화.

        repo: DupeScanRepository | None. None 이면 영구화 없이 종래 동작
        (테스트 친화 + DupeScanOverviewScreen 거치지 않는 직접 호출 대비).

        start_idx: 어느 cluster 부터 시작할지. DupeScanOverviewScreen 에서
        특정 row 클릭하면 해당 index 로 진입.
        """
        super().__init__()
        self._clusters = list(clusters)
        self._client = client
        self._session = session
        self._delete_callback = delete_callback
        self._repo = repo
        # 현재 보고 있는 cluster 의 index — start_idx 로 진입 가능.
        self._idx: int = max(0, min(start_idx, max(0, len(self._clusters) - 1)))
        # 각 cluster 의 entry_id → bool ("✓ = 삭제 대상" 여부).
        # 초기값: keep_suggestion 만 False (보존), 나머지 True (삭제).
        self._marks: list[dict[str, bool]] = []
        for c in self._clusters:
            keep = c.keep_suggestion
            self._marks.append({
                str(e.get("entry_id") or ""): (
                    str(e.get("entry_id") or "") != keep
                )
                for e in c.entries
            })
        # 한 건이라도 삭제 성공하면 True → 호출자가 refresh.
        self._dirty: bool = False
        # 이미 처리된 cluster idx 집합 — 재방문 시 readonly 안내용.
        self._processed: set[int] = set()
        # 테스트 친화.
        self.last_status: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="scan-frame"):
            yield Static("[bold]중복 거래 검사[/bold]", id="scan-title")
            yield Static("", id="scan-progress")
            yield Static("", id="scan-reasons")
            yield Static(
                "Space: ✓/✗ toggle · Enter: 확정 → 다음 · n/→: skip · "
                "p/←: 이전 · Esc: 닫기",
                id="scan-hint",
            )
            yield DataTable(id="scan-table", cursor_type="row")
            yield Static("", id="scan-status")
            with Horizontal(id="scan-buttons"):
                yield Button("확정 (Enter)", id="scan-btn-confirm",
                             variant="warning")
                yield Button("skip (n)", id="scan-btn-skip")
                yield Button("닫기 (Esc)", id="scan-btn-close")

    def on_mount(self) -> None:
        if not self._clusters:
            # 빈 cluster — 사용자에게 알리고 자동 닫기.
            self.query_one("#scan-progress", Static).update(
                "✅  중복 후보 없음",
            )
            self.query_one("#scan-progress", Static).add_class("different")
            self.query_one("#scan-hint", Static).update(
                "선택한 기간의 거래에서 중복으로 보이는 거래를 찾지 못했습니다. "
                "Esc / Enter 로 닫기.",
            )
            self.query_one("#scan-table", DataTable).styles.display = "none"
            try:
                self.query_one("#scan-btn-close", Button).focus()
            except Exception:  # pragma: no cover
                pass
            return
        self._render_current()
        try:
            self.query_one("#scan-table", DataTable).focus()
        except Exception:  # pragma: no cover
            pass

    # ---- rendering ------------------------------------------------------

    def _render_current(self) -> None:
        """현재 idx 의 cluster 정보 + table 갱신."""
        if not self._clusters:
            return
        n = len(self._clusters)
        i = self._idx
        c = self._clusters[i]
        prog = self.query_one("#scan-progress", Static)
        # 강도 라벨 + 색상.
        for v in ("identical", "very_likely", "possible", "different"):
            prog.remove_class(v)
        prog.add_class(c.verdict)
        v_label = VERDICT_LABELS_KO.get(c.verdict, c.verdict)
        processed_mark = "  · 처리됨" if i in self._processed else ""
        prog.update(f"{i + 1} / {n}  ·  {v_label}{processed_mark}")

        reasons_w = self.query_one("#scan-reasons", Static)
        if c.reasons:
            reasons_w.update("근거: " + " · ".join(c.reasons))
        else:
            reasons_w.update("")

        self._render_table()

    def _render_table(self) -> None:
        from whooing_core.dupes import is_tui_auto_imported
        table = self.query_one("#scan-table", DataTable)
        table.clear(columns=True)
        # ✓ 컬럼 = 삭제 대상 표시 (DuplicateEvalScreen 은 "★ = keep" 으로
        # 반대 — 본 화면은 cluster 안 여러 건 삭제가 흔하므로 명시적 "삭제"
        # 마크가 사용자 의도와 일치). CL #53092+: 입력 출처 (사람/자동) 도
        # 새 컬럼으로 표시 — keep / delete 추천 근거 가시화.
        table.add_column("삭제", width=6)
        table.add_column("출처", width=8)
        table.add_column("날짜", width=10)
        table.add_column("금액", width=14)
        table.add_column("왼쪽", width=14)
        table.add_column("오른쪽", width=14)
        table.add_column("적요/메모")

        c = self._clusters[self._idx]
        marks = self._marks[self._idx]
        for e in c.entries:
            eid = str(e.get("entry_id") or "")
            mark = "[red]✓ 삭제[/red]" if marks.get(eid) else "[green]✗ 보존[/green]"
            origin = (
                "[dim]🤖 자동[/dim]"
                if is_tui_auto_imported(e)
                else "[cyan]👤 사람[/cyan]"
            )
            l_name = self._account_title(e.get("l_account_id") or "")
            r_name = self._account_title(e.get("r_account_id") or "")
            item = str(e.get("item") or "")
            memo = str(e.get("memo") or "")
            label = item
            if memo and memo != item:
                label = f"{item}  ·  {memo}" if item else memo
            table.add_row(
                mark,
                origin,
                str(e.get("entry_date") or "")[:8],
                _fmt_money(e.get("money")),
                l_name,
                r_name,
                label,
                key=eid,
            )

    def _account_title(self, account_id: str) -> str:
        if not account_id:
            return ""
        for a in self._session.accounts_flat:
            if a.get("account_id") == account_id:
                return str(a.get("title") or account_id)
        return account_id

    # ---- key actions ----------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(self._dirty)

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#scan-table", DataTable).action_cursor_up()
        except Exception:  # pragma: no cover
            pass

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#scan-table", DataTable).action_cursor_down()
        except Exception:  # pragma: no cover
            pass

    def action_toggle_delete(self) -> None:
        """Space — 현재 row 의 ✓/✗ toggle."""
        if not self._clusters:
            return
        eid = self._cursor_entry_id()
        if not eid:
            return
        marks = self._marks[self._idx]
        marks[eid] = not marks.get(eid, False)
        self._render_table()
        # cursor 위치 복원.
        try:
            table = self.query_one("#scan-table", DataTable)
            row_idx = next(
                (i for i, e in enumerate(self._clusters[self._idx].entries)
                 if str(e.get("entry_id") or "") == eid),
                None,
            )
            if row_idx is not None:
                table.move_cursor(row=row_idx)
        except Exception:  # pragma: no cover
            pass

    def action_next_cluster(self) -> None:
        """n / → — 삭제 없이 다음 cluster."""
        if not self._clusters:
            return
        if self._idx + 1 >= len(self._clusters):
            self._set_status("마지막 cluster 입니다 (Esc 로 닫기).")
            return
        self._idx += 1
        self._render_current()

    def action_prev_cluster(self) -> None:
        """p / ← — 이전 cluster."""
        if not self._clusters:
            return
        if self._idx == 0:
            self._set_status("첫 cluster 입니다.")
            return
        self._idx -= 1
        self._render_current()

    def action_confirm(self) -> None:
        """Enter — 현재 cluster 의 ✓ 표시된 거래 모두 삭제 → 다음 cluster."""
        if not self._clusters:
            self.dismiss(self._dirty)
            return
        marks = self._marks[self._idx]
        to_delete = [eid for eid, mark in marks.items() if mark]
        if not to_delete:
            self._set_status(
                "삭제 대상이 없습니다 — Space 로 ✓ 표시 후 Enter, 또는 n 으로 skip.",
                error=True,
            )
            return
        # 안전망 — cluster 전체 삭제 가능하지만 사용자가 모르고 그럴 가능성
        # 낮음. 경고 (모두 ✓ 인 경우) — 그래도 진행은 허용.
        if len(to_delete) == len(marks):
            self._set_status(
                f"⚠️  cluster {len(marks)}건 전부 삭제 진행…",
            )
        else:
            self._set_status(f"⏳ 삭제 진행 중… ({len(to_delete)}건)")
        self._delete_worker(to_delete)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "scan-btn-close":
            self.dismiss(self._dirty)
        elif bid == "scan-btn-confirm":
            self.action_confirm()
        elif bid == "scan-btn-skip":
            self.action_next_cluster()

    def _cursor_entry_id(self) -> str | None:
        try:
            table = self.query_one("#scan-table", DataTable)
            row = table.cursor_row
            entries = self._clusters[self._idx].entries
            if row is None or row < 0 or row >= len(entries):
                return None
            return str(entries[row].get("entry_id") or "")
        except Exception:  # pragma: no cover
            return None

    def _set_status(self, msg: str, *, error: bool = False) -> None:
        self.last_status = msg
        try:
            s = self.query_one("#scan-status", Static)
            s.update(msg)
            if error:
                s.add_class("error")
            else:
                s.remove_class("error")
        except Exception:  # pragma: no cover
            pass

    # ---- delete worker --------------------------------------------------

    @work(exclusive=True, group="dupe_scan_delete", name="dupe_scan_delete")
    async def _delete_worker(self, entry_ids: list[str]) -> None:
        if self._delete_callback is not None:
            try:
                deleted, failed = await self._delete_callback(entry_ids)
            except Exception as e:  # pragma: no cover
                log.exception("dupe scan delete callback failed")
                self._set_status(f"삭제 실패 (INTERNAL): {e}", error=True)
                return
        else:
            deleted, failed = await self._delete_via_client(entry_ids)
        if deleted > 0:
            self._dirty = True
            self._processed.add(self._idx)
            # CL #52989+: StoredCluster + repo 가 주어졌으면 영구화.
            self._mark_resolved_in_repo(self._clusters[self._idx])
        if failed:
            self._set_status(
                f"{deleted}건 삭제, {len(failed)}건 실패 — 첫 실패: {failed[0]}",
                error=True,
            )
            # 실패 row 들은 ✓ 유지 (사용자가 재시도 / skip 결정).
            self._render_current()
            return
        # 전부 성공 → 다음 pending cluster 자동 이동 (없으면 닫기).
        self._set_status(f"✅ {deleted}건 삭제 완료.")
        next_idx = self._next_unresolved_idx(self._idx + 1)
        if next_idx is None:
            # 더 정리할 cluster 없음 — 사용자 OK 없이도 dismiss.
            self.dismiss(self._dirty)
            return
        self._idx = next_idx
        self._render_current()

    def _mark_resolved_in_repo(self, cluster: Any) -> None:
        """StoredCluster + repo 가 있을 때만 status='resolved' 영구화."""
        if self._repo is None:
            return
        cluster_id = getattr(cluster, "id", None)
        if cluster_id is None:
            return
        try:
            self._repo.update_status(int(cluster_id), "resolved")
        except Exception:  # pragma: no cover
            log.exception("repo.update_status failed for cluster %s", cluster_id)

    def _next_unresolved_idx(self, from_idx: int) -> int | None:
        """from_idx 부터 처음 발견되는 unresolved cluster idx.

        StoredCluster 면 .status == 'pending' 만 후보. 일반 DupeCluster
        (._processed 만으로 판단) 면 from_idx 자체가 마지막이면 None.
        """
        n = len(self._clusters)
        if from_idx >= n:
            return None
        for i in range(from_idx, n):
            c = self._clusters[i]
            status = getattr(c, "status", None)
            if status == "resolved":
                continue
            if i in self._processed:
                continue
            return i
        return None

    async def _delete_via_client(
        self, entry_ids: list[str],
    ) -> tuple[int, list[str]]:
        # CL #53110+: entry_date 를 함께 넘겨 delete_entry 가 오류 시 실제
        # 삭제 여부를 검증(idempotent)하게 한다 — 공식 MCP 가 삭제를 적용한
        # 뒤에도 isError 를 반환하는 사례에서 '오삭제 실패' 오보고 방지.
        date_by_id: dict[str, str] = {}
        cluster = self._clusters[self._idx] if self._clusters else None
        for e in getattr(cluster, "entries", ()) or ():
            eid = str(e.get("entry_id") or "")
            if eid:
                date_by_id[eid] = str(e.get("entry_date") or "")
        deleted = 0
        failed: list[str] = []
        for eid in entry_ids:
            try:
                await self._client.delete_entry(
                    section_id=self._session.section_id,
                    entry_id=eid,
                    entry_date=date_by_id.get(eid) or None,
                )
                deleted += 1
            except ToolError as e:
                failed.append(f"{eid} [{e.kind}] {e.message}")
            except Exception as e:  # pragma: no cover
                log.exception("dupe scan delete %s failed", eid)
                failed.append(f"{eid} INTERNAL: {e}")
        return deleted, failed
