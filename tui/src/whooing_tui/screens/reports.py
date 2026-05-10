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
from textual.containers import Vertical, VerticalScroll
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


def _build_menu() -> list[MenuItem]:
    """(item_id, display label, fetch coroutine factory) 의 list.

    CL #51117 부터: WhooingClient 의 endpoint-별 메서드 호출.
    `cashflow` 은 실 API 에 대응 endpoint 없어 메뉴에서 제외.
    """

    async def fetch_balance_sheet(client, session):
        # 자산 + 부채 (capital 자동 계산), 현재 시점 (rows_type=none).
        return await client.get_report(
            section_id=session.section_id,
            account="assets,liabilities", rows_type="none",
        )

    async def fetch_pl_summary(client, session):
        s, e = _month_start_today()
        return await client.get_report_summary(
            section_id=session.section_id,
            account="expenses,income",
            start_date=s, end_date=e, rows_type="none",
        )

    async def fetch_monthly_trend(client, session):
        s, e = _ytd()
        # PL flat 시계열 (수익/지출/순이익 월별).
        return await client.get_report_summary(
            section_id=session.section_id, account="expenses,income",
            rows_type="month", start_date=s, end_date=e,
        )

    async def fetch_in_out(client, session):
        s, e = _month_start_today()
        return await client.get_in_out(
            section_id=session.section_id, start_date=s, end_date=e,
        )

    async def fetch_calendar(client, session):
        s, e = _month_start_today()
        return await client.get_calendar(
            section_id=session.section_id, start_date=s, end_date=e,
        )

    async def fetch_entries_latest(client, session):
        return await client.get_entries_latest(
            section_id=session.section_id, limit=20,
        )

    async def fetch_custom_bs(client, session):
        return await client.list_report_customs(
            section_id=session.section_id, report="report_bs",
        )

    async def fetch_custom_pl(client, session):
        return await client.list_report_customs(
            section_id=session.section_id, report="report_pl",
        )

    async def fetch_budget_expenses(client, session):
        return await client.get_budget(
            section_id=session.section_id, account="expenses",
        )

    async def fetch_budget_income(client, session):
        return await client.get_budget(
            section_id=session.section_id, account="income",
        )

    async def fetch_budget_goal(client, session):
        return await client.get_budget_goal(section_id=session.section_id)

    return [
        ("balance_sheet", "재무상태표 (자산/부채/자본 — 현재)", fetch_balance_sheet),
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

    dismiss((item_id, label)) — 호출자가 어떤 항목을 골랐는지 받아 별도
    `ReportResultScreen` 을 push 한다. 두 모달을 분리한 이유는 fetch 가
    오래 걸릴 수 있어 메뉴 모달은 빠르게 닫히고 결과 모달이 spinner /
    로딩 status 를 띄우게 하기 위함.
    """

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
        except Exception as e:  # pragma: no cover
            log.exception("report fetch failed")
            self.last_error = f"INTERNAL: {e}"
            self._show_error(self.last_error)
            return
        self.last_payload = payload
        self._render_payload(payload)

    def _show_error(self, msg: str) -> None:
        try:
            status = self.query_one("#reports-result-status", Static)
            status.update(msg)
            status.add_class("error")
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


# ---- payload 포매터 ---------------------------------------------------


def format_report_payload(item_id: str, payload: Any) -> str:
    """보고서 종류별 사람-친화 평문 (Rich markup OK).

    Phase 1 은 raw JSON pretty dump 가 baseline — 종류별 전용 렌더는 후속
    CL 에서 증분.
    """
    if payload is None:
        return "[dim](응답 없음)[/dim]"

    # balance_sheet / pl_summary 등은 dict, customs / entries_latest 는 list.
    # 양쪽 모두 indent JSON 으로 dump — UTF-8 보존.
    try:
        return "```\n" + json.dumps(
            payload, ensure_ascii=False, indent=2, default=str,
        ) + "\n```"
    except Exception:
        return repr(payload)
