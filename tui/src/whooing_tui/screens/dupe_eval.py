"""중복 거래 평가 화면 (CL #52815+).

사용자 요청:
> 둘 이상의 거래내역을 선택한 다음 m을 누르면 컨텍스트메뉴에 '중복인지
> 평가' 메뉴를 추가해주세요. 이 메뉴를 선택하면 선택된 거래들이 서로
> 중복인지 평가해 결과를 알려주는 기능을 추가해주세요. 중복이라도 여러
> 가지 방식으로 다르게 입력되어 있을 수 있으니 다양한 방법으로 중복
> 여부를 평가해야 합니다. 그래서 중복일 경우 하나만 선택해 남기고
> 나머지를 삭제하는 인터페이스를 제공해야 합니다. 또한 중복이 아니라면
> 중복이 아님을 표시하고 팝업을 닫도록 해주세요.

흐름:
  1. EntriesScreen 의 m 키 → context menu 에 "중복인지 평가" 항목 추가
     (selection >= 2 일 때만).
  2. 선택 → `DuplicateEvalScreen` push — `whooing_core.dupes.evaluate_
     duplicates(entries)` 호출, verdict / reasons / pairs / keep_suggestion
     렌더.
  3. verdict == "different" 면: "중복이 아닙니다" 메시지 + Esc/Enter 로 닫기.
  4. verdict in (identical, very_likely, possible) 면: DataTable 로 거래
     목록 + 각 row 에 keep radio. 사용자가 keep 선택 후 "선택만 남기고
     삭제" 버튼 → 나머지 삭제 worker 실행 → EntriesScreen refresh.

본 화면은 sqlite 의 annotation 도 함께 정리 (`_purge_local` callback) —
호출자가 callback 으로 주입.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from whooing_core.dupes import (
    VERDICT_LABELS_KO,
    DupeReport,
    evaluate_duplicates,
)

from whooing_tui.client import WhooingClient
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


from whooing_tui.text_utils import fmt_money as _fmt_money


class DuplicateEvalScreen(ModalScreen[bool | None]):
    """선택된 거래의 중복 평가 + dedup 인터페이스.

    dismiss 값:
      - None     — Esc 로 닫음 (변경 없음).
      - False    — 중복 아님 OK 클릭 (변경 없음).
      - True     — dedup 실행 완료 (호출자가 refresh).
    """

    DEFAULT_CSS = """
    DuplicateEvalScreen {
        align: center middle;
    }
    #dupe-frame {
        width: 95%;
        max-width: 140;
        min-width: 60;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #dupe-title {
        height: 1;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    #dupe-verdict {
        height: auto;
        margin-top: 1;
        padding: 0 1;
    }
    #dupe-verdict.identical, #dupe-verdict.very_likely {
        color: $warning;
        text-style: bold;
    }
    #dupe-verdict.possible {
        color: $accent;
    }
    #dupe-verdict.different {
        color: $success;
        text-style: bold;
    }
    #dupe-reasons {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #dupe-pairs {
        height: auto;
        max-height: 8;
        padding: 0 1;
        margin-top: 1;
        color: $text-muted;
    }
    #dupe-list-label {
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }
    #dupe-table {
        height: auto;
        max-height: 12;
        margin-top: 0;
    }
    #dupe-hint {
        height: 1;
        margin-top: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #dupe-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    #dupe-buttons Button {
        margin: 0 1;
    }
    #dupe-status {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #dupe-status.error {
        color: $error;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("up", "cursor_up", "↑", show=False),
        Binding("down", "cursor_down", "↓", show=False),
        Binding("enter", "confirm", "Keep selected", show=True),
        # CL #52818+: 사용자 요청 — 하이라이트한 행에서 space 누르면 그
        # 한 항목만 keep (나머지 자동 해제). `_keep_id` 가 단일 string 이라
        # set_keep 호출 자체가 곧 "오직 이것" 의미 — 별도 toggle 없음.
        # priority=True 로 DataTable 의 기본 space 핸들러 (cursor_type="row"
        # 에선 거의 noop 이지만 안전망) 보다 우선.
        Binding("space", "set_keep", "Keep this", show=True, priority=True),
        *bind_ko("k", "set_keep", "Keep", show=False),
    ]

    def __init__(
        self,
        entries: list[dict[str, Any]],
        *,
        client: WhooingClient,
        session: SessionState,
        delete_callback: Callable[[list[str]], Awaitable[tuple[int, list[str]]]] | None = None,
    ) -> None:
        super().__init__()
        self._entries = list(entries)
        self._client = client
        self._session = session
        # 호출자가 sqlite 의 annotation 도 정리하도록 — TUI 의 _purge_local
        # 호출 래퍼. 없으면 후잉 API 만 정리.
        self._delete_callback = delete_callback
        # 평가는 push 직후 on_mount 에서 즉시 계산 (pure func, 빠름).
        self._report: DupeReport | None = None
        # keep 으로 선택된 entry_id (radio 의 선택값).
        self._keep_id: str | None = None
        # 테스트 친화 — 마지막 status / 평가 결과.
        self.last_status: str = ""
        self.last_verdict: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dupe-frame"):
            yield Static("[bold]중복 거래 평가[/bold]", id="dupe-title")
            yield Static("평가 중…", id="dupe-verdict")
            yield Static("", id="dupe-reasons")
            yield Static("", id="dupe-pairs")
            yield Static(
                "↑/↓ 이동 · Space (또는 k) 로 남길 거래 선택 · Enter 로 확정 (나머지 삭제)",
                id="dupe-list-label",
            )
            yield DataTable(id="dupe-table", cursor_type="row")
            yield Static("", id="dupe-status")
            with Horizontal(id="dupe-buttons"):
                yield Button("선택만 남기고 삭제", id="dupe-btn-dedup",
                             variant="warning")
                yield Button("닫기", id="dupe-btn-close")

    def on_mount(self) -> None:
        # 1) 평가 — pure func, 즉시.
        report = evaluate_duplicates(self._entries)
        self._report = report
        self.last_verdict = report.verdict

        verdict = self.query_one("#dupe-verdict", Static)
        verdict.remove_class("identical")
        verdict.remove_class("very_likely")
        verdict.remove_class("possible")
        verdict.remove_class("different")
        verdict.add_class(report.verdict)
        label = VERDICT_LABELS_KO.get(report.verdict, report.verdict)

        # 2) "중복 아님" 분기 — 사용자 요청: 표시하고 팝업 닫도록.
        # 닫기는 사용자가 직접 누르도록 — 자동 dismiss 면 결과를 못 본다.
        if report.verdict == "different":
            verdict.update(f"✅  {label}")
            self.query_one("#dupe-reasons", Static).update(
                "선택된 거래들 사이에서 중복 가능성을 찾지 못했습니다.",
            )
            self._hide_dedup_ui()
            self.query_one("#dupe-table", DataTable).styles.display = "none"
            self.query_one("#dupe-list-label", Static).update("")
            self.last_status = "중복 아님."
            # 닫기 버튼에 자동 focus — Enter 로 닫기.
            try:
                self.query_one("#dupe-btn-close", Button).focus()
            except Exception:  # pragma: no cover
                pass
            return

        # 3) 중복 가능 — verdict + reasons + pairs.
        verdict.update(f"⚠️  {label}")
        if report.reasons:
            self.query_one("#dupe-reasons", Static).update(
                "근거: " + " · ".join(report.reasons),
            )
        self._render_pairs(report)
        self._render_entries_table(report)
        # 기본 keep 후보 highlight + 강조.
        self._keep_id = report.keep_suggestion
        self._highlight_keep()
        try:
            self.query_one("#dupe-table", DataTable).focus()
        except Exception:  # pragma: no cover
            pass

    def _hide_dedup_ui(self) -> None:
        """중복 아님일 때 dedup 버튼 / pairs 영역 숨김."""
        try:
            self.query_one("#dupe-pairs", Static).update("")
            self.query_one("#dupe-btn-dedup", Button).styles.display = "none"
        except Exception:  # pragma: no cover
            pass

    def _render_pairs(self, report: DupeReport) -> None:
        """각 쌍의 verdict 를 짧게 — 가장 강한 매칭만 먼저, 최대 5개.

        목록이 길면 첫 5개 + "외 N개" 로 축약.
        """
        if not report.pairs:
            return
        sorted_pairs = sorted(
            report.pairs,
            key=lambda p: -_verdict_rank(p[2]),
        )
        lines: list[str] = []
        for a_id, b_id, v, reasons in sorted_pairs[:5]:
            label = VERDICT_LABELS_KO.get(v, v)
            r = " — " + " · ".join(reasons) if reasons else ""
            lines.append(f"  • {a_id} ↔ {b_id}: {label}{r}")
        extra = len(sorted_pairs) - 5
        if extra > 0:
            lines.append(f"  • … 외 {extra}쌍")
        self.query_one("#dupe-pairs", Static).update(
            "쌍별 평가:\n" + "\n".join(lines),
        )

    def _render_entries_table(self, report: DupeReport) -> None:
        table = self.query_one("#dupe-table", DataTable)
        table.clear(columns=True)
        table.add_column("keep", width=4)
        table.add_column("date", width=10)
        table.add_column("money", width=14)
        table.add_column("left", width=12)
        table.add_column("right", width=12)
        table.add_column("item")
        for e in self._entries:
            eid = str(e.get("entry_id") or "")
            mark = "★" if eid == report.keep_suggestion else " "
            l_name = self._account_title(e.get("l_account_id") or "")
            r_name = self._account_title(e.get("r_account_id") or "")
            table.add_row(
                mark,
                str(e.get("entry_date") or "")[:8],
                _fmt_money(e.get("money")),
                l_name,
                r_name,
                str(e.get("item") or ""),
                key=eid,
            )

    def _account_title(self, account_id: str) -> str:
        """SessionState 의 양방향 인덱스로 title 조회 — 없으면 id 그대로."""
        if not account_id:
            return ""
        for a in self._session.accounts_flat:
            if a.get("account_id") == account_id:
                return str(a.get("title") or account_id)
        return account_id

    def _highlight_keep(self) -> None:
        """현재 _keep_id 의 row 에 ★ 마크 — 다른 row 는 빈칸."""
        table = self.query_one("#dupe-table", DataTable)
        for e in self._entries:
            eid = str(e.get("entry_id") or "")
            mark = "★" if eid == self._keep_id else " "
            try:
                table.update_cell(eid, "keep", mark)
            except Exception:  # pragma: no cover
                pass

    # ---- key actions ----------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        try:
            table = self.query_one("#dupe-table", DataTable)
            table.action_cursor_up()
        except Exception:  # pragma: no cover
            pass

    def action_cursor_down(self) -> None:
        try:
            table = self.query_one("#dupe-table", DataTable)
            table.action_cursor_down()
        except Exception:  # pragma: no cover
            pass

    def action_set_keep(self) -> None:
        """k — 현재 cursor row 를 keep 으로 지정."""
        eid = self._cursor_entry_id()
        if not eid:
            return
        self._keep_id = eid
        self._highlight_keep()
        self.set_status(f"남길 거래: {eid}")

    def action_confirm(self) -> None:
        """Enter — 현재 cursor row 를 keep 으로 지정하고 dedup 실행."""
        if self._report is None or self._report.verdict == "different":
            self.dismiss(False)
            return
        eid = self._cursor_entry_id()
        if eid:
            self._keep_id = eid
            self._highlight_keep()
        self._dedup_kickoff()

    def on_data_table_row_selected(
        self, event: DataTable.RowSelected,
    ) -> None:
        """row click — keep 지정만 (자동 dedup 안 함, 사용자가 버튼 / Enter 로 확정)."""
        key = event.row_key.value if event.row_key else None
        if key:
            self._keep_id = str(key)
            self._highlight_keep()
            self.set_status(f"남길 거래: {key} — Enter 또는 버튼으로 확정")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dupe-btn-close":
            self.dismiss(False)
        elif event.button.id == "dupe-btn-dedup":
            self._dedup_kickoff()

    def _cursor_entry_id(self) -> str | None:
        try:
            table = self.query_one("#dupe-table", DataTable)
            row = table.cursor_row
            if row is None or row < 0 or row >= len(self._entries):
                return None
            return str(self._entries[row].get("entry_id") or "")
        except Exception:  # pragma: no cover
            return None

    def set_status(self, msg: str, *, error: bool = False) -> None:
        self.last_status = msg
        try:
            s = self.query_one("#dupe-status", Static)
            s.update(msg)
            if error:
                s.add_class("error")
            else:
                s.remove_class("error")
        except Exception:  # pragma: no cover
            pass

    # ---- dedup worker ---------------------------------------------------

    def _dedup_kickoff(self) -> None:
        if self._report is None:
            return
        if self._report.verdict == "different":
            self.dismiss(False)
            return
        if not self._keep_id:
            self.set_status("남길 거래를 선택하세요 (↑/↓ + k 또는 클릭).", error=True)
            return
        to_delete = [
            str(e.get("entry_id") or "")
            for e in self._entries
            if str(e.get("entry_id") or "") and str(e.get("entry_id")) != self._keep_id
        ]
        if not to_delete:
            self.set_status("삭제할 거래가 없습니다 (모두 keep).", error=True)
            return
        self.set_status(f"삭제 진행 중… ({len(to_delete)}건)")
        self._dedup_worker(to_delete)

    @work(exclusive=True, group="dupe_dedup", name="dupe_dedup")
    async def _dedup_worker(self, entry_ids: list[str]) -> None:
        if self._delete_callback is not None:
            try:
                deleted, failed = await self._delete_callback(entry_ids)
            except Exception as e:  # pragma: no cover
                log.exception("dedup callback failed")
                self.set_status(f"삭제 실패 (INTERNAL): {e}", error=True)
                return
        else:
            deleted, failed = await self._delete_via_client(entry_ids)
        if failed:
            self.set_status(
                f"{deleted}건 삭제, {len(failed)}건 실패 — 첫 실패: {failed[0]}",
                error=True,
            )
            return
        self.set_status(f"{deleted}건 삭제 완료. {self._keep_id} 만 남음.")
        # 사용자가 결과를 잠시 볼 수 있게 — 자동 dismiss 안 함, 닫기 버튼으로.
        # 호출자가 refresh 하도록 True dismiss.
        self.dismiss(True)

    async def _delete_via_client(
        self, entry_ids: list[str],
    ) -> tuple[int, list[str]]:
        """fallback — delete_callback 없이 client 만 사용. 실패한 eid 모음."""
        deleted = 0
        failed: list[str] = []
        # 감사 §3-D: 평가 대상 entry 의 날짜를 알고 있으므로 넘겨 해당
        # 윈도우만 캐시 무효화.
        date_by_id = {
            str(e.get("entry_id") or ""): (e.get("entry_date") or None)
            for e in self._entries
        }
        for eid in entry_ids:
            try:
                await self._client.delete_entry(
                    section_id=self._session.section_id, entry_id=eid,
                    entry_date=date_by_id.get(eid),
                )
                deleted += 1
            except ToolError as e:
                failed.append(f"{eid} [{e.kind}] {e.message}")
            except Exception as e:  # pragma: no cover
                log.exception("delete %s failed", eid)
                failed.append(f"{eid} INTERNAL: {e}")
        return deleted, failed


def _verdict_rank(v: str) -> int:
    return {
        "different": 0, "possible": 1, "very_likely": 2, "identical": 3,
    }.get(v, 0)
