"""AccountFlowScreen — 특정 항목의 흐름/변동 분석 (0.84.0).

로드맵 P2-B. "이 항목(계정과목)이 어디로 흘렀나 / 어떻게 변했나" 분석.
하나의 항목(account_id)을 고른 뒤 네 가지 시각을 좌측 메뉴로 전환:

  - 아이템별 금액   (entries_items_of_account_id)
  - 거래처별        (entries_clients_of_account_id)
  - 잔액 변화 추이  (entries_changes_of_account_id)
  - 상대 계정 흐름  (entries_flow_of_account_id)

모두 공식 후잉 MCP `report-get` 위임. 응답 shape 이 타입별로 달라
`_render_flow` 가 `{aggregate, rows}` / list / dict 를 관대하게 표로 dump
(reports.py 와 동일 철학 — 안 맞으면 raw JSON fallback).

진입: EntriesScreen 이 항목을 먼저 고른 뒤 (AccountPickerScreen) 본 화면을
account/account_id 와 함께 push.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from whooing_tui.client import WhooingClient
from whooing_tui.dates import today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.screens.reports import _fmt_money, _kr, _table
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)

# (report-get type, 라벨).
_ANALYSES: list[tuple[str, str]] = [
    ("entries_items_of_account_id", "아이템별 금액"),
    ("entries_clients_of_account_id", "거래처별"),
    ("entries_changes_of_account_id", "잔액 변화 추이"),
    ("entries_flow_of_account_id", "상대 계정 흐름"),
]


def _render_flow(payload: Any) -> str:
    """flow/changes/items 응답을 관대하게 표로. 안 맞으면 raw JSON.

    공통 shape 은 `{aggregate: {...}, rows: {...|[...]}}`. aggregate 는
    요약 key-value, rows 는 기간/항목별 금액으로 본다.
    """
    if payload is None:
        return "[yellow](응답 없음)[/yellow]"
    if isinstance(payload, dict) and not payload:
        return "[yellow](결과 없음 — 빈 응답)[/yellow]"

    parts: list[str] = []
    if isinstance(payload, dict):
        agg = payload.get("aggregate")
        if isinstance(agg, dict) and agg:
            rows = [
                [_kr(str(k)),
                 _fmt_money(v) if isinstance(v, (int, float)) else str(v)]
                for k, v in agg.items()
            ]
            parts.append("[bold]요약[/bold]\n" + _table(
                ["구분", "값"], rows, right_align={1}))

        rows_obj = payload.get("rows")
        if isinstance(rows_obj, dict) and rows_obj:
            rrows: list[list[str]] = []
            for k in sorted(rows_obj.keys()):
                v = rows_obj[k]
                if isinstance(v, dict):
                    money = (v.get("money") or v.get("total")
                             or v.get("margin") or v.get("balance") or "")
                    rrows.append(
                        [str(k), _fmt_money(money) if money != "" else ""])
                else:
                    rrows.append([str(k), _fmt_money(v)])
            if rrows:
                parts.append("[bold]상세[/bold]\n" + _table(
                    ["기간/항목", "금액"], rrows, right_align={1}))
        elif isinstance(rows_obj, list) and rows_obj:
            parts.append(_render_list(rows_obj))

        if parts:
            return "\n\n".join(parts)

    if isinstance(payload, list):
        if not payload:
            return "[yellow](결과 없음 — 0건)[/yellow]"
        return _render_list(payload)

    # fallback: raw JSON.
    try:
        return "```\n" + json.dumps(
            payload, ensure_ascii=False, indent=2, default=str) + "\n```"
    except Exception:  # pragma: no cover
        return repr(payload)


def _render_list(items: list[Any]) -> str:
    """list[dict] → 공통 키 표. money/total 류는 우측 정렬."""
    dicts = [it for it in items if isinstance(it, dict)]
    if not dicts:
        return "```\n" + json.dumps(items, ensure_ascii=False, default=str) + "\n```"
    # 표시할 키: item/client/title 류 + money/total 류 우선.
    name_keys = [k for k in ("item", "client", "title", "account_id", "name")
                 if any(k in d for d in dicts)]
    money_keys = [k for k in ("money", "total", "margin", "balance")
                  if any(k in d for d in dicts)]
    cols = (name_keys[:2] + money_keys[:2]) or list(dicts[0].keys())[:4]
    rows: list[list[str]] = []
    for d in dicts:
        row = []
        for k in cols:
            v = d.get(k, "")
            row.append(_fmt_money(v) if k in money_keys else str(v)[:24])
        rows.append(row)
    right = {i for i, k in enumerate(cols) if k in money_keys}
    return _table([_kr(k) for k in cols], rows, right_align=right)


class AccountFlowScreen(ModalScreen[None]):
    """항목 흐름/변동 분석 — 좌측 분석 메뉴 + 우측 결과."""

    BINDINGS = [
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    AccountFlowScreen { align: center middle; }
    #af_frame {
        width: 95%;
        max-width: 150;
        min-width: 50;
        height: 90%;
        max-height: 45;
        min-height: 16;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
        layout: vertical;
    }
    #af_title { height: 1; content-align: center middle; color: $accent; }
    #af_body { height: 1fr; }
    #af_menu { width: 28; border-right: solid $primary; }
    #af_result { width: 1fr; padding: 0 1; }
    #af_status { height: auto; color: $text-muted; }
    #af_status.error { color: $error; }
    """

    def __init__(
        self,
        client: WhooingClient,
        session: SessionState,
        *,
        account: str,
        account_id: str,
        title: str = "",
    ) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self._account = account
        self._account_id = account_id
        self._acc_title = title or account_id
        self.last_status: str = ""
        self._current_type = _ANALYSES[0][0]
        # ↑/↓ 분석 전환 디바운스 타이머 — 빠른 이동 시 마지막만 fetch.
        self._fetch_timer: Any = None

    def compose(self) -> ComposeResult:
        with Vertical(id="af_frame"):
            yield Static(
                f"[bold]항목 흐름 분석 — {self._acc_title} "
                f"[dim]{self._account_id}[/dim][/bold]",
                id="af_title",
            )
            with Horizontal(id="af_body"):
                yield OptionList(id="af_menu")
                with VerticalScroll(id="af_result"):
                    yield Static("", id="af_result_body")
            yield Static("", id="af_status")

    def on_mount(self) -> None:
        # DOM 이 안정된 뒤 setup — 깊게 중첩된 OptionList 가 on_mount 시점에
        # 아직 mount 안 됐을 수 있는 race 회피 (call_after_refresh).
        self.call_after_refresh(self._setup)

    def _setup(self) -> None:
        menu = self.query_one("#af_menu", OptionList)
        for rtype, label in _ANALYSES:
            menu.add_option(Option(label, id=rtype))
        menu.highlighted = 0
        menu.focus()
        self._fetch(self._current_type)

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        self.dismiss(None)

    def action_refresh(self) -> None:
        self._fetch(self._current_type)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        rtype = event.option.id
        if rtype and rtype != self._current_type:
            self._current_type = rtype
            # 디바운스 (감사 2026-06 §2-C 신규): ↑/↓ 홀드로 메뉴를 지나칠 때
            # 매 highlight 마다 네트워크 fetch 하지 않고 멈춘 뒤 1회만.
            if self._fetch_timer is not None:
                self._fetch_timer.stop()
            self._set_status(f"⏳ … ({rtype})")
            self._fetch_timer = self.set_timer(
                0.25, lambda: self._fetch(self._current_type),
            )

    @work(exclusive=True, group="af-fetch", name="fetch")
    async def _fetch(self, rtype: str) -> None:
        self._set_status(f"⏳ 조회 중… ({rtype})")
        today = today_yyyymmdd()
        start = today[:6] + "01"
        args = {
            "type": rtype,
            "section_id": self._session.section_id,
            "account": self._account,
            "account_id": self._account_id,
            "start_date": start, "end_date": today,
        }
        try:
            res = await self._client.call_official_tool("report-get", args)
        except ToolError as e:
            self._set_status(f"조회 실패 [{e.kind}] {e.message}", error=True)
            self._show(f"[red]조회 실패: {e.message}[/red]")
            return
        except Exception as ex:  # pragma: no cover
            self._set_status(f"조회 실패: {ex}", error=True)
            self._show(f"[red]조회 실패: {ex}[/red]")
            return
        if self._current_type != rtype:
            return  # 그사이 다른 분석으로 이동 — 결과 폐기.
        self._show(_render_flow(res))
        self._set_status("↑/↓ 분석 전환 · r 새로고침 · q 뒤로")

    # ---- helpers ---------------------------------------------------------

    def _show(self, markup: str) -> None:
        self.query_one("#af_result_body", Static).update(markup)

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.last_status = text
        bar = self.query_one("#af_status", Static)
        bar.update(text)
        bar.remove_class("error")
        if error:
            bar.add_class("error")
