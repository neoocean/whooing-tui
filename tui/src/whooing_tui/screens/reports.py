"""후잉 통계 / 보고서 뷰 — 드롭다운 메뉴 + 결과 팝업 (CL #51116+).

사용자 요청:

> 후잉에서 제공하는 여러 가지 통계 뷰들을 지원해주세요. 각각의 통계는
> 풀다운 메뉴를 통해 접근하고 메뉴를 선택하면 팝업을 통해 결과를
> 보여줘야 합니다.

흐름:
  1. EntriesScreen 단축키 `t` (또는 한글 ㅌ) → `ReportsMenuScreen` push.
  2. OptionList 에서 보고서 종류 선택 → 화면 dismiss + 그 보고서를
     `ReportResultScreen` 으로 새로 push.
  3. ReportResultScreen 이 worker 로 후잉 API fetch → 결과 표시.
  4. ReportResultScreen 안에서 Esc/q 면 EntriesScreen 으로 복귀.

지원 보고서 (Phase 1):
  * 통합 재무 보고서 (report-get type=report) — 자산 / 부채 / 자본 /
    수입 / 지출 누적·합계.
  * 기간별 추이 (report-get type=report rows_type=month) — 월별 시계열.
  * 손익/자산 요약 (report-get type=report_summary).
  * 현금흐름표 (report-get type=cashflow).
  * 캘린더 (report-get type=calendar).
  * 최근 거래 (report-get type=entries_latest).
  * 사용자 정의 BS / PL (report_customs-list).
  * 예산 대비 실적 (budget-get pl=expenses / income).
  * 장기목표 설정 (budget_goal-get).

응답 shape 가 다양해 일단 raw dict / list 를 사람-친화 평문으로 dump —
mermaid 차트나 인터랙티브 표는 후속.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from whooing_tui.client import WhooingClient
from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


# 메뉴 항목 — id 는 dispatch 키, label 은 사용자에게 보여주는 이름.
# fetch 함수는 client + session 을 받아 await 결과 반환.
def _today() -> str:
    return today_yyyymmdd()


def _month_start_today() -> tuple[str, str]:
    """이번 달 1일 ~ 오늘. 후잉 보고서 기본 기간."""
    today = today_yyyymmdd()
    return (today[:6] + "01", today)


def _ytd() -> tuple[str, str]:
    """이번 해 1월 1일 ~ 오늘."""
    today = today_yyyymmdd()
    return (today[:4] + "0101", today)


# (label, fetch_fn) — fetch_fn(client, session) → awaitable.
MenuItem = tuple[str, str, Callable[..., Any]]


def _ym_start_today() -> tuple[str, str]:
    """이번 달 (YYYYMM, YYYYMM) — budget-get 의 start/end 가 6자리 (월) 단위."""
    today = today_yyyymmdd()
    return (today[:6], today[:6])


def _build_menu() -> list[MenuItem]:
    """(item_id, display label, fetch coroutine factory) 의 list.

    **CL #52755+: 모든 보고서 fetch 가 후잉 공식 MCP server 위임.**

    종전엔 client.py 의 자체 REST path 추측 (`/report/{account}.json` 등)
    이라 일부 endpoint 에서 403 ("비-JSON 응답") 실패. 후잉 공식 MCP
    server (`https://whooing.com/mcp`) 가 모든 도구 + 정확한 schema 를
    노출 — 그것을 `client.call_official_tool(name, args)` 로 호출하면
    path 추측 자체가 사라짐.

    주요 schema 차이 (공식 MCP 기준):
      - report-get: 모든 보고서가 단일 도구, `type` 파라미터 분기.
      - account: enum (assets/liabilities/capital/expenses/income/all) —
        콤마 다중 X. 본 메뉴는 'all' 로 통합 조회.
      - budget-get: start_date/end_date 가 **YYYYMM** (6자리, 월).
      - report 류 start_date/end_date 는 YYYYMMDD (8자리).
    """

    async def fetch_balance_sheet(client, session):
        # 재무상태표 — 전 계정 (자산/부채/자본/수입/지출) 현재 시점 합계.
        return await client.call_official_tool("report-get", {
            "type": "report",
            "section_id": session.section_id,
            "account": "all",
            "rows_type": "none",
        })

    async def fetch_pl_summary(client, session):
        s, e = _month_start_today()
        return await client.call_official_tool("report-get", {
            "type": "report_summary",
            "section_id": session.section_id,
            "account": "all",
            "start_date": s, "end_date": e,
            "rows_type": "none",
        })

    async def fetch_monthly_trend(client, session):
        s, e = _ytd()
        return await client.call_official_tool("report-get", {
            "type": "report_summary",
            "section_id": session.section_id,
            "account": "all",
            "start_date": s, "end_date": e,
            "rows_type": "month",
        })

    async def fetch_in_out(client, session):
        s, e = _month_start_today()
        return await client.call_official_tool("report-get", {
            "type": "in_out",
            "section_id": session.section_id,
            "start_date": s, "end_date": e,
        })

    async def fetch_calendar(client, session):
        s, e = _month_start_today()
        return await client.call_official_tool("report-get", {
            "type": "calendar",
            "section_id": session.section_id,
            "start_date": s, "end_date": e,
        })

    async def fetch_entries_latest(client, session):
        return await client.call_official_tool("report-get", {
            "type": "entries_latest",
            "section_id": session.section_id,
            "limit": 20,
        })

    async def fetch_custom_bs(client, session):
        return await client.call_official_tool("report_customs-list", {
            "section_id": session.section_id,
            "report": "report_bs",
        })

    async def fetch_custom_pl(client, session):
        return await client.call_official_tool("report_customs-list", {
            "section_id": session.section_id,
            "report": "report_pl",
        })

    async def fetch_budget_expenses(client, session):
        s, e = _ym_start_today()  # YYYYMM
        return await client.call_official_tool("budget-get", {
            "section_id": session.section_id,
            "pl": "expenses",
            "start_date": s, "end_date": e,
        })

    async def fetch_budget_income(client, session):
        s, e = _ym_start_today()  # YYYYMM
        return await client.call_official_tool("budget-get", {
            "section_id": session.section_id,
            "pl": "income",
            "start_date": s, "end_date": e,
        })

    async def fetch_budget_goal(client, session):
        return await client.call_official_tool("budget_goal-get", {
            "section_id": session.section_id,
        })

    return [
        ("balance_sheet", "재무상태표 (전 계정 — 현재)", fetch_balance_sheet),
        ("pl_summary", "손익 요약 (이번 달)", fetch_pl_summary),
        ("monthly_trend", "월별 추이 (YTD)", fetch_monthly_trend),
        ("in_out", "항목별 증감 (이번 달)", fetch_in_out),
        ("calendar", "캘린더 (이번 달)", fetch_calendar),
        ("entries_latest", "최근 거래 20건", fetch_entries_latest),
        ("custom_bs", "사용자 정의 BS", fetch_custom_bs),
        ("custom_pl", "사용자 정의 PL", fetch_custom_pl),
        ("budget_expenses", "예산 대비 실적 — 지출", fetch_budget_expenses),
        ("budget_income", "예산 대비 실적 — 수입", fetch_budget_income),
        ("budget_goal", "장기목표 설정", fetch_budget_goal),
    ]


class ReportsMenuScreen(ModalScreen[tuple[str, str] | None]):
    """보고서 종류 선택 모달.

    CL #52790+ (사용자 요청): 메뉴 항목 선택 시 자체 `push_screen
    (ReportResultScreen)` 호출 — 결과 모달이 Esc 로 닫히면 textual stack
    의 이전 화면 (= 본 메뉴) 으로 자동 복귀. 사용자가 메뉴에서 다른 항목
    선택 가능. 종전엔 dismiss((id, label)) 로 외부 callback 에 위임,
    Esc 가 EntriesScreen 까지 한 번에 닫혀 사용자가 메뉴 재진입 부담.

    dismiss 값: `None` 만 (Esc/취소). 항목 선택 결과는 dismiss 가 아닌
    내부 push 로 처리.
    """

    def __init__(self, client=None, session=None) -> None:
        """`client` + `session` 을 받아 ReportResultScreen 직접 push.

        backward compat: 둘 다 None 이면 종전 dismiss 흐름 (테스트 등에서
        활용 가능).
        """
        super().__init__()
        self._client = client
        self._session = session

    DEFAULT_CSS = """
    ReportsMenuScreen {
        align: center middle;
    }
    #reports-menu-frame {
        /* CL #51120+: 좁은 터미널 대응. */
        width: 95%;
        max-width: 60;
        min-width: 30;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #reports-menu-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #reports-menu-hint {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    OptionList {
        height: auto;
        max-height: 18;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        *bind_ko("q", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="reports-menu-frame"):
            yield Static("[bold]보고서 / 통계[/bold]", id="reports-menu-title")
            yield Static(
                "↑/↓ 이동 / Enter 선택 / Esc 취소", id="reports-menu-hint",
            )
            yield OptionList(id="reports-menu-list")

    def on_mount(self) -> None:
        opt = self.query_one("#reports-menu-list", OptionList)
        for item_id, label, _fetch in _build_menu():
            opt.add_option(Option(label, id=item_id))
        if opt.option_count:
            opt.highlighted = 0
        opt.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id
        if not oid:
            return
        # label 도 함께 — 결과 화면 타이틀용.
        for item_id, label, _ in _build_menu():
            if item_id == oid:
                # CL #52790+: client/session 이 있으면 자체 push, 없으면
                # 종전 dismiss 흐름 (backward compat).
                if self._client is not None and self._session is not None:
                    self.app.push_screen(
                        ReportResultScreen(
                            self._client, self._session,
                            item_id=oid, label=label,
                        ),
                    )
                else:
                    self.dismiss((oid, label))
                return


class ReportResultScreen(ModalScreen[None]):
    """선택한 보고서를 fetch + 표시하는 결과 모달.

    fetch 는 worker 로 백그라운드 — 화면 push 와 동시에 시작, 응답 도착
    하면 컨텐츠 갱신. 에러는 적색 표시. 후잉 응답 shape 가 종류별로 달라
    Phase 1 은 raw JSON 을 pretty-print 로 dump (사람-친화 textual hint
    + 추후 종류별 전용 렌더로 점진 교체).
    """

    DEFAULT_CSS = """
    ReportResultScreen {
        align: center middle;
    }
    #reports-result-frame {
        /* CL #51120+: 좁은 터미널 대응. height 도 95% 로 (대부분 화면 채움). */
        width: 95%;
        max-width: 100;
        min-width: 30;
        height: 95%;
        max-height: 36;
        min-height: 12;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #reports-result-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #reports-result-status {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #reports-result-status.error {
        color: $error;
    }
    #reports-result-body {
        height: 1fr;
    }
    #reports-result-content {
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        *bind_ko("q", "close", "Close", show=True),
    ]

    def __init__(
        self,
        client: WhooingClient,
        session: SessionState,
        *,
        item_id: str,
        label: str,
    ) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self._item_id = item_id
        self._label = label
        # 마지막 결과 / 에러 — 테스트 친화.
        self.last_payload: Any = None
        self.last_error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="reports-result-frame"):
            yield Static(f"[bold]{self._label}[/bold]", id="reports-result-title")
            yield Static("로딩 중…", id="reports-result-status")
            with VerticalScroll(id="reports-result-body"):
                yield Static("", id="reports-result-content")

    def on_mount(self) -> None:
        self._fetch()

    def action_close(self) -> None:
        self.dismiss(None)

    @work(exclusive=True, group="reports", name="fetch_report")
    async def _fetch(self) -> None:
        # _build_menu 에서 fetch_fn 회수.
        fetch_fn = None
        for iid, _label, fn in _build_menu():
            if iid == self._item_id:
                fetch_fn = fn
                break
        if fetch_fn is None:
            self._show_error("내부 오류: 알 수 없는 메뉴 항목.")
            return
        try:
            payload = await fetch_fn(self._client, self._session)
        except ToolError as e:
            self.last_error = f"[{e.kind}] {e.message}"
            self._show_error(self.last_error)
            return
        except Exception as e:
            # CL #52755+: OfficialMcpError 도 ToolError 와 같은 분기로
            # — 사용자에게 같은 ERROR 메시지 표면. 단 import 는 lazy.
            from whooing_tui.official_mcp import OfficialMcpError
            if isinstance(e, OfficialMcpError):
                code = f" (code={e.code})" if e.code is not None else ""
                self.last_error = f"[MCP{code}] {e}"
                self._show_error(self.last_error)
                return
            log.exception("report fetch failed")
            self.last_error = f"INTERNAL: {e}"
            self._show_error(self.last_error)
            return
        self.last_payload = payload
        self._render_payload(payload)

    def _show_error(self, msg: str) -> None:
        """CL #52753+: 에러 메시지를 status (좁은 한 줄) + body (큰 영역) 양쪽에.

        종전엔 status 한 줄만 갱신 — 사용자가 body 의 빈 공간만 보고 "빈
        화면" 으로 인식했음 (사용자 보고). body 에도 같은 메시지를 큼지막
        하게 + Esc 안내까지 같이 노출.
        """
        try:
            status = self.query_one("#reports-result-status", Static)
            status.update("⚠️ 에러 — 본문 참조")
            status.add_class("error")
            content = self.query_one("#reports-result-content", Static)
            content.update(
                f"[red bold]에러[/red bold]\n\n"
                f"[red]{msg}[/red]\n\n"
                f"[dim]Esc / q 로 닫고 다른 보고서를 시도해 보세요.[/dim]"
            )
        except Exception:  # pragma: no cover
            pass

    def _render_payload(self, payload: Any) -> None:
        try:
            status = self.query_one("#reports-result-status", Static)
            status.update("완료. Esc / q 로 닫기.")
            status.remove_class("error")
            content = self.query_one("#reports-result-content", Static)
            content.update(format_report_payload(self._item_id, payload))
        except Exception:  # pragma: no cover
            pass


class ReportsScreen(ModalScreen[None]):
    """CL #52792+ (사용자 요청): 좌측 메뉴 + 우측 결과를 한 큰 모달에 표시.

    종전엔 `ReportsMenuScreen` (메뉴) → 선택 → `ReportResultScreen` (결과)
    두 모달 — 메뉴를 잠시 닫고 결과만 보여 다른 항목 시도가 번거로움.
    본 클래스는 두 패널을 동시 표시:
      - 좌측 OptionList: 11개 보고서 항목.
      - 우측 VerticalScroll > Static: 현재 highlight 된 항목의 결과.
      - ↑/↓ 이동 → 자동 fetch (worker exclusive group="reports_fetch").
      - Enter: 강제 refresh (선택적).
      - Esc / q: 닫고 EntriesScreen 복귀.

    백그라운드 fetch 는 `@work(exclusive=True)` 라 빠른 ↑/↓ 이동 시 이전
    fetch 가 자동 cancel.

    기존 `ReportsMenuScreen` / `ReportResultScreen` 은 backward compat 으로
    유지 — 본 새 클래스는 사용자 가시 default.
    """

    DEFAULT_CSS = """
    ReportsScreen {
        align: center middle;
    }
    #reports-frame {
        width: 95%;
        max-width: 160;
        min-width: 50;
        height: 95%;
        max-height: 50;
        min-height: 16;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #reports-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #reports-hint {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #reports-body {
        layout: horizontal;
        height: 1fr;
        padding-top: 1;
    }
    #reports-menu-pane {
        width: 30;
        min-width: 24;
        height: 1fr;
        border-right: solid $primary;
        padding: 0 1 0 0;
    }
    #reports-content-pane {
        width: 1fr;
        height: 1fr;
        padding: 0 0 0 1;
    }
    #reports-status {
        height: 1;
        color: $text-muted;
    }
    #reports-status.error {
        color: $error;
    }
    #reports-content {
        padding: 1 0;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        *bind_ko("q", "close", "Close", show=True),
    ]

    def __init__(
        self,
        client: WhooingClient,
        session: SessionState,
    ) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self._current_item_id: str | None = None
        # 마지막 결과 / 에러 — 테스트 친화.
        self.last_payload: Any = None
        self.last_error: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="reports-frame"):
            yield Static("[bold]보고서 / 통계[/bold]", id="reports-title")
            yield Static(
                "↑/↓ 이동 → 자동 조회 / Esc 닫기",
                id="reports-hint",
            )
            with Horizontal(id="reports-body"):
                with Vertical(id="reports-menu-pane"):
                    yield OptionList(id="reports-menu-list")
                with Vertical(id="reports-content-pane"):
                    yield Static("로딩 중…", id="reports-status")
                    with VerticalScroll():
                        yield Static("", id="reports-content")

    def on_mount(self) -> None:
        opt = self.query_one("#reports-menu-list", OptionList)
        for item_id, label, _ in _build_menu():
            opt.add_option(Option(label, id=item_id))
        if opt.option_count:
            opt.highlighted = 0
            # 첫 항목 자동 fetch.
            first = _build_menu()[0]
            self._fetch_for(first[0], first[1])
        opt.focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        """↑/↓ 이동 — 자동 fetch (exclusive worker)."""
        oid = event.option.id
        if not oid or oid == self._current_item_id:
            return
        for item_id, label, _ in _build_menu():
            if item_id == oid:
                self._fetch_for(item_id, label)
                return

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        """Enter — 강제 refresh."""
        oid = event.option.id
        if not oid:
            return
        for item_id, label, _ in _build_menu():
            if item_id == oid:
                self._fetch_for(item_id, label, force=True)
                return

    def _fetch_for(
        self, item_id: str, label: str, *, force: bool = False,
    ) -> None:
        """worker spawn — fetch + 우측 panel update."""
        self._current_item_id = item_id
        # 우측 status — "로딩 중…".
        try:
            status = self.query_one("#reports-status", Static)
            status.update(f"[dim]{label} — 로딩 중…[/dim]")
            status.remove_class("error")
            content = self.query_one("#reports-content", Static)
            if force:
                content.update("[dim]재조회 중…[/dim]")
        except Exception:  # pragma: no cover
            pass
        self._fetch_worker(item_id, label)

    @work(exclusive=True, group="reports_fetch", name="reports_fetch")
    async def _fetch_worker(self, item_id: str, label: str) -> None:
        fetch_fn = None
        for iid, _label, fn in _build_menu():
            if iid == item_id:
                fetch_fn = fn
                break
        if fetch_fn is None:
            self._show_error(label, "알 수 없는 메뉴 항목.")
            return
        try:
            payload = await fetch_fn(self._client, self._session)
        except ToolError as e:
            self.last_error = f"[{e.kind}] {e.message}"
            self._show_error(label, self.last_error)
            return
        except Exception as e:
            from whooing_tui.official_mcp import OfficialMcpError
            if isinstance(e, OfficialMcpError):
                code = f" (code={e.code})" if e.code is not None else ""
                self.last_error = f"[MCP{code}] {e}"
                self._show_error(label, self.last_error)
                return
            log.exception("report fetch failed: %s", item_id)
            self.last_error = f"INTERNAL: {e}"
            self._show_error(label, self.last_error)
            return
        # 다른 항목으로 이동했으면 결과 폐기.
        if self._current_item_id != item_id:
            return
        self.last_payload = payload
        self.last_error = None
        self._render_payload(item_id, label, payload)

    def _show_error(self, label: str, msg: str) -> None:
        try:
            status = self.query_one("#reports-status", Static)
            status.update(f"⚠️ {label} — 본문 참조")
            status.add_class("error")
            content = self.query_one("#reports-content", Static)
            content.update(
                f"[red bold]에러[/red bold]\n\n[red]{msg}[/red]\n\n"
                f"[dim]↑/↓ 로 다른 보고서 시도, Esc 로 닫기.[/dim]"
            )
        except Exception:  # pragma: no cover
            pass

    def _render_payload(
        self, item_id: str, label: str, payload: Any,
    ) -> None:
        try:
            status = self.query_one("#reports-status", Static)
            status.update(f"[dim]{label} — 완료[/dim]")
            status.remove_class("error")
            content = self.query_one("#reports-content", Static)
            content.update(format_report_payload(item_id, payload))
        except Exception:  # pragma: no cover
            pass


# ---- payload 포매터 ---------------------------------------------------


def _fmt_money(v: Any) -> str:
    """천단위 콤마 + 음수 빨강 + 0은 dim. Rich markup 포함."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return str(v)
    s = f"{n:,}"
    if n < 0:
        return f"[red]{s}[/red]"
    if n == 0:
        return f"[dim]{s}[/dim]"
    return s


def _fmt_money_plain(v: Any) -> str:
    """markup 없는 콤마 천 단위 — 정렬 시 cell 폭 계산 용."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return str(v)
    return f"{n:,}"


def _fmt_yyyymmdd(s: Any) -> str:
    """후잉 entry_date (YYYYMMDD 또는 YYYYMMDD.NNNN) → YYYY-MM-DD."""
    raw = str(s or "").split(".", 1)[0]
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _fmt_yyyymm(s: Any) -> str:
    """YYYYMM → YYYY-MM."""
    raw = str(s or "")
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}"
    return raw


# 후잉 account 키 → 한글 라벨. 보고서 가독성 위해 통일.
_ACCOUNT_KR: dict[str, str] = {
    "assets": "자산",
    "liabilities": "부채",
    "capital": "자본",
    "income": "수입",
    "expenses": "지출",
    "total": "합계",
    "net_income": "순이익",
    "etc": "기타",
    "balance": "잔액",
    "in": "수입",
    "out": "지출",
    "margin": "순증감",
    "budget": "예산",
    "money": "사용",
    "remains": "잔여",
}


def _kr(key: str) -> str:
    return _ACCOUNT_KR.get(key, key)


def _table(
    headers: list[str],
    rows: list[list[str]],
    *,
    right_align: set[int] | None = None,
) -> str:
    """간단한 텍스트 표 — Rich markup 보존. right_align 으로 우측 정렬할
    column index set."""
    right_align = right_align or set()
    # 각 column 폭: markup strip 한 plain 길이의 max.
    import re as _re
    strip = _re.compile(r"\[/?[^\]]+\]")
    def plain_len(s: str) -> int:
        return _wide_len(strip.sub("", s))
    widths = [plain_len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            if i >= len(widths):
                widths.append(plain_len(cell))
            else:
                widths[i] = max(widths[i], plain_len(cell))

    def pad(cell: str, w: int, right: bool) -> str:
        gap = w - plain_len(cell)
        return (" " * gap) + cell if right else cell + (" " * gap)

    lines = []
    header_line = "  ".join(
        f"[bold]{pad(h, widths[i], i in right_align)}[/bold]"
        for i, h in enumerate(headers)
    )
    lines.append(header_line)
    sep = "  ".join("─" * widths[i] for i in range(len(headers)))
    lines.append(f"[dim]{sep}[/dim]")
    for r in rows:
        row_cells = []
        for i, cell in enumerate(r):
            w = widths[i] if i < len(widths) else plain_len(cell)
            row_cells.append(pad(cell, w, i in right_align))
        lines.append("  ".join(row_cells))
    return "\n".join(lines)


def _wide_len(s: str) -> int:
    """한글 등 wide char 는 2 cell. ASCII 1 cell."""
    return sum(2 if ord(c) > 0x7F else 1 for c in s)


# ---- 종류별 renderer (각각 dict / list 검증 후 string 반환, 실패시 None) -


def _render_balance_or_pl(payload: Any) -> str | None:
    """balance_sheet / pl_summary — flat dict {assets, liabilities, capital,
    expenses, income, net_income, total} 형태. 캡처의 실 응답 shape.
    """
    if not isinstance(payload, dict):
        return None
    keys = ("assets", "liabilities", "capital", "income", "expenses",
            "total", "net_income")
    rows: list[list[str]] = []
    for k in keys:
        if k not in payload:
            continue
        v = payload[k]
        if isinstance(v, dict):
            v = v.get("total") or v.get("balance") or v.get("money") or v
        rows.append([_kr(k), _fmt_money(v)])
    if not rows:
        return None
    return _table(["항목", "금액 (KRW)"], rows, right_align={1})


def _render_monthly_trend(payload: Any) -> str | None:
    """monthly_trend — rows_type=month: `{rows_type, rows: {YYYYMM: {...}}}`.
    각 월의 핵심 4개 (assets/liabilities/income/expenses) 만 추출 + 순이익.
    """
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows")
    if not isinstance(rows, dict) or not rows:
        return None
    cols = ["월", "수입", "지출", "순이익"]
    out_rows: list[list[str]] = []
    for ym in sorted(rows.keys()):
        m = rows[ym]
        if not isinstance(m, dict):
            continue
        income = m.get("income") or 0
        expenses = m.get("expenses") or 0
        try:
            net = int(float(income or 0)) - int(float(expenses or 0))
        except (TypeError, ValueError):
            net = 0
        out_rows.append([
            _fmt_yyyymm(ym), _fmt_money(income), _fmt_money(expenses),
            _fmt_money(net),
        ])
    if not out_rows:
        return None
    return _table(cols, out_rows, right_align={1, 2, 3})


def _render_in_out(payload: Any) -> str | None:
    """in_out — `{<account>: {total: {in, out, margin, balance}}, ...}` 형태."""
    if not isinstance(payload, dict):
        return None
    rows: list[list[str]] = []
    for acc in ("assets", "liabilities", "capital", "income", "expenses"):
        if acc not in payload:
            continue
        v = payload[acc]
        if not isinstance(v, dict):
            continue
        total = v.get("total") if isinstance(v.get("total"), dict) else v
        rows.append([
            _kr(acc),
            _fmt_money(total.get("in", 0)),
            _fmt_money(total.get("out", 0)),
            _fmt_money(total.get("margin", 0)),
            _fmt_money(total.get("balance", 0)),
        ])
    if not rows:
        return None
    return _table(
        ["계정", "수입", "지출", "순증감", "잔액"], rows,
        right_align={1, 2, 3, 4},
    )


def _render_calendar(payload: Any) -> str | None:
    """calendar — `{aggregate: {count, income, expenses, etc}, rows: {date: {...}}}`."""
    if not isinstance(payload, dict):
        return None
    agg = payload.get("aggregate") or {}
    rows = payload.get("rows") or {}
    parts: list[str] = []
    if isinstance(agg, dict) and agg:
        parts.append("[bold]이번 달 합계[/bold]")
        for k in ("income", "expenses", "etc", "count"):
            if k not in agg:
                continue
            label = _kr(k) if k != "count" else "거래 건수"
            v = agg[k]
            if k == "count":
                parts.append(f"  {label}: {v}")
            else:
                parts.append(f"  {label}: {_fmt_money(v)}")
    if isinstance(rows, dict) and rows:
        parts.append("")
        out_rows: list[list[str]] = []
        for d in sorted(rows.keys()):
            r = rows[d] or {}
            if not isinstance(r, dict):
                continue
            inc = r.get("income", 0)
            exp = r.get("expenses", 0)
            if (inc or 0) == 0 and (exp or 0) == 0:
                continue
            out_rows.append([
                _fmt_yyyymmdd(d), _fmt_money(inc), _fmt_money(exp),
            ])
        if out_rows:
            parts.append(_table(
                ["일자", "수입", "지출"], out_rows, right_align={1, 2},
            ))
    return "\n".join(parts) if parts else None


def _render_entries_latest(payload: Any) -> str | None:
    """entries_latest — list of {entry_id, entry_date, l_account*, r_account*,
    money, item, ...}.
    """
    if not isinstance(payload, list) or not payload:
        return None
    out_rows: list[list[str]] = []
    for e in payload:
        if not isinstance(e, dict):
            continue
        left = e.get("l_account_title") or e.get("l_account") or ""
        right = e.get("r_account_title") or e.get("r_account") or ""
        item = e.get("item") or ""
        out_rows.append([
            _fmt_yyyymmdd(e.get("entry_date")),
            _fmt_money(e.get("money")),
            str(left)[:14],
            str(right)[:14],
            str(item)[:24],
        ])
    if not out_rows:
        return None
    return _table(
        ["일자", "금액", "차변", "대변", "적요"], out_rows,
        right_align={1},
    )


def _render_budget(payload: Any) -> str | None:
    """budget — `{aggregate: {total, total_steady, total_floating, misc: {today,
    daily_remains, weekly_remains, standard, possibility}}}`.

    CL #52790+: 사용자 캡처의 실 응답 (예산 대비 실적 — 수입) shape 반영.
    표:
      구분            예산      사용      잔여
      ────────────  ──────  ──────  ──────
      전체              ...
      정기 (steady)    ...
      유동 (floating)  ...
      오늘              ...
    + 하단 misc 요약 한 줄.
    """
    if not isinstance(payload, dict):
        return None
    agg = payload.get("aggregate")
    if not isinstance(agg, dict):
        return None

    # 메인 표 — 4 row (전체 / 정기 / 유동 / 오늘).
    def _bucket(d: Any) -> tuple[Any, Any, Any]:
        if not isinstance(d, dict):
            return (None, None, None)
        return d.get("budget"), d.get("money"), d.get("remains")

    rows: list[list[str]] = []
    sources = [
        ("전체", agg.get("total")),
        ("정기", agg.get("total_steady")),
        ("유동", agg.get("total_floating")),
    ]
    misc = agg.get("misc") if isinstance(agg.get("misc"), dict) else {}
    if isinstance(misc.get("today"), dict):
        sources.append(("오늘", misc["today"]))

    for label, src in sources:
        if src is None:
            continue
        b, m, r = _bucket(src)
        rows.append([label, _fmt_money(b), _fmt_money(m), _fmt_money(r)])

    if not rows:
        return None
    main = _table(
        ["구분", "예산", "사용", "잔여"], rows,
        right_align={1, 2, 3},
    )

    # 보조 정보: misc 의 daily/weekly/standard/possibility — 한 줄.
    extras: list[str] = []
    if "daily_remains" in misc:
        extras.append(f"일별 잔여 {_fmt_money(misc['daily_remains'])}")
    if "weekly_remains" in misc:
        extras.append(f"주별 잔여 {_fmt_money(misc['weekly_remains'])}")
    if "standard" in misc:
        extras.append(f"기준 {_fmt_money(misc['standard'])}")
    if "possibility" in misc:
        try:
            pct = int(misc["possibility"])
            extras.append(f"달성 가능성 {pct}%")
        except (TypeError, ValueError):
            extras.append(f"달성 가능성 {misc['possibility']}")
    if extras:
        return main + "\n\n[dim]" + "  /  ".join(extras) + "[/dim]"
    return main


def _render_budget_goal(payload: Any) -> str | None:
    """budget_goal — `{set_id, base_ym, goal_ym, goal_money, base_money, ...}`."""
    if not isinstance(payload, dict):
        return None
    set_id = payload.get("set_id")
    if not set_id or set_id == 0:
        return "[yellow](장기목표 미설정)[/yellow]\n\n[dim]후잉 웹에서 먼저 설정하세요.[/dim]"
    rows: list[list[str]] = []
    if "base_ym" in payload and "goal_ym" in payload:
        rows.append([
            "기간",
            f"{_fmt_yyyymm(payload['base_ym'])} ~ {_fmt_yyyymm(payload['goal_ym'])}",
        ])
    for k_src, label in (
        ("base_money", "시작 자산"),
        ("goal_money", "목표 자산"),
        ("base_income", "연간 수입 예산"),
        ("base_expenses", "연간 지출 예산"),
    ):
        if k_src in payload and payload[k_src] is not None:
            rows.append([label, _fmt_money(payload[k_src])])
    if not rows:
        return None
    return _table(["항목", "값"], rows, right_align={1})


def _render_report_customs(payload: Any) -> str | None:
    """report_customs-list — `{rows: [...]}` or `[...]` 직접."""
    if isinstance(payload, dict):
        items = payload.get("rows")
    else:
        items = payload
    if not isinstance(items, list):
        return None
    if not items:
        # 빈 list — top-level "빈 결과" 안내 분기가 안 잡는 케이스
        # (payload 가 `{rows: []}` 형태로 rows 키만 있음 — len(payload)=1).
        return (
            "[yellow](결과 없음)[/yellow]\n\n"
            "[dim]사용자 정의 BS/PL 행이 정의 안 됨. 후잉 웹에서 먼저 "
            "정의하세요.[/dim]"
        )
    out_rows: list[list[str]] = []
    for r in items:
        if not isinstance(r, dict):
            continue
        title = r.get("title") or r.get("name") or ""
        money = r.get("money", 0)
        out_rows.append([str(title)[:30], _fmt_money(money)])
    if not out_rows:
        return None
    return _table(["행 제목", "금액 (KRW)"], out_rows, right_align={1})


_RENDERERS: dict[str, Any] = {
    "balance_sheet": _render_balance_or_pl,
    "pl_summary": _render_balance_or_pl,
    "monthly_trend": _render_monthly_trend,
    "in_out": _render_in_out,
    "calendar": _render_calendar,
    "entries_latest": _render_entries_latest,
    "custom_bs": _render_report_customs,
    "custom_pl": _render_report_customs,
    "budget_expenses": _render_budget,
    "budget_income": _render_budget,
    "budget_goal": _render_budget_goal,
}


def format_report_payload(item_id: str, payload: Any) -> str:
    """보고서 종류별 사람-친화 평문 (Rich markup OK).

    CL #52786+: item_id 별 전용 renderer (`_RENDERERS`) 가 표 형태로
    포매팅. renderer 가 None 반환 (응답 shape mismatch) 면 raw JSON
    fallback. raw JSON 보다 가독성 큰 폭 향상.

    CL #52753+: None / 빈 list / 빈 dict 의 경우 명확한 안내 메시지.
    종전엔 `[]` / `{}` 만 표시돼 사용자가 "빈 화면" 으로 인식.
    """
    if payload is None:
        return (
            "[yellow](응답 없음 — 후잉 API 가 None 반환)[/yellow]\n\n"
            "[dim]토큰 권한 또는 endpoint path 문제일 수 있습니다.[/dim]"
        )
    if isinstance(payload, list) and len(payload) == 0:
        return (
            "[yellow](결과 없음)[/yellow]\n\n"
            "[dim]이 조건/기간에 해당하는 데이터가 0건입니다. "
            "사용자 정의 보고서면 후잉 웹에서 먼저 정의가 필요합니다.[/dim]"
        )
    if isinstance(payload, dict) and len(payload) == 0:
        return (
            "[yellow](결과 없음 — 응답 dict 가 비어있음)[/yellow]\n\n"
            "[dim]후잉 API 가 빈 응답을 반환했습니다.[/dim]"
        )

    # CL #52786+: item_id 별 전용 렌더 시도.
    renderer = _RENDERERS.get(item_id)
    if renderer is not None:
        try:
            rendered = renderer(payload)
            if rendered:
                return rendered
        except Exception:
            log.exception("renderer for %s failed", item_id)

    # Fallback: raw JSON.
    try:
        return "```\n" + json.dumps(
            payload, ensure_ascii=False, indent=2, default=str,
        ) + "\n```"
    except Exception:
        return repr(payload)
