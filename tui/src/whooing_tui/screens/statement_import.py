"""StatementImportScreen — 카드 명세서 (HTML / PDF) 를 후잉에 import.

Wizard 단계:
  1. 입력: file_path 가 주어진 상태로 진입 (또는 home 에서 'i' 로 진입 후 prompt)
  2. detect: issuer 자동 (html_adapters / pdf_adapters)
  3. unlock: HTML 인 경우 password (env 또는 modal)
  4. extract: 어댑터 호출 → CSVRow list
  5. dedup: ledger 와 비교 (±N일, 같은 amount + similar merchant)
  6. preview: matched / proposed 표시 + 카테고리 편집
  7. confirm: 사용자 OK → entries-create loop + statement_import_log

각 단계가 실패해도 wizard 는 계속 동작 (사용자가 password 다시 시도 등).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from whooing_core import attachments as core_attach
from whooing_core import db as core_db
from whooing_core.csv_adapters.base import CSVRow
from whooing_core.html_adapters import detect as html_detect
from whooing_core.html_adapters import known_issuers as html_known_issuers
from whooing_core.html_adapters.hanacard_secure_mail import (
    parse_html_async as parse_hanacard_async,
)
from whooing_core.html_adapters.hyundaicard_secure_mail import (
    parse_html_async as parse_hyundaicard_async,
)
from whooing_core.pdf_adapters import detect as pdf_detect
from whooing_core.pdf_adapters import parse as pdf_parse

from whooing_tui import data as tui_data
from whooing_tui.client import WhooingClient

log = logging.getLogger(__name__)


# ---- Password modal --------------------------------------------------


class PasswordModal(ModalScreen[str | None]):
    """6자리 보안메일 패스워드 입력 modal. 결과는 dismiss 시 string 반환."""

    BINDINGS = [Binding("escape", "cancel", "취소")]

    def __init__(self, hint: str = "생년월일 6자리 (YYMMDD)") -> None:
        super().__init__()
        self.hint = hint

    def compose(self) -> ComposeResult:
        with Container(id="pw_box"):
            yield Label(f"카드 보안메일 패스워드 — {self.hint}")
            yield Input(
                placeholder="000000", password=True, max_length=6, id="pw_input",
            )
            with Horizontal():
                yield Button("OK", id="pw_ok", variant="primary")
                yield Button("Cancel", id="pw_cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pw_ok":
            inp = self.query_one("#pw_input", Input)
            self.dismiss(inp.value or None)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---- Helpers --------------------------------------------------------


def _detect_format(path: str) -> tuple[str, str | None]:
    """('html', issuer) | ('pdf', issuer) | (kind, None) 반환.

    auto-detect 실패 시 (kind, None). 확장자 우선이라 .html/.pdf 가 결정적.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    if suffix in (".html", ".htm"):
        d = html_detect(str(p))
        return ("html", d.detected_issuer)
    if suffix == ".pdf":
        d = pdf_detect(str(p))
        return ("pdf", d.detected_issuer)
    raise ValueError(f"지원하지 않는 확장자: {suffix} (.html / .pdf 만)")


async def _extract_rows(
    file_path: str, kind: str, issuer: str, password: str | None,
) -> list[CSVRow]:
    """kind/issuer 에 따라 적절한 어댑터 호출 → CSVRow list."""
    if kind == "html":
        if issuer == "hanacard_secure_mail":
            return await parse_hanacard_async(file_path, password or "")
        if issuer == "hyundaicard_secure_mail":
            return await parse_hyundaicard_async(file_path, password or "")
        raise ValueError(f"미지원 HTML issuer: {issuer}")
    if kind == "pdf":
        _used, rows = pdf_parse(file_path, issuer=issuer)
        return rows
    raise ValueError(f"미지원 kind: {kind}")


def _dedup(
    rows: list[CSVRow],
    ledger: list[dict[str, Any]],
    tolerance_days: int = 2,
    *,
    section_id: str | None = None,
    check_import_log: bool = True,
) -> tuple[list[dict[str, Any]], list[CSVRow], list[CSVRow]]:
    """(matched_existing, previously_imported, new_proposals).

    CL #51129+ dedup 강화: 종전엔 ledger 매칭 (현재 후잉 거래) 만 했으나,
    `statement_import_log` 의 자연키 매칭 (date+amount+merchant) 도 검사 —
    같은 명세서를 두 번 import 하거나 두 명세서가 동일 거래를 보고할 때
    **이미 import 했음** 안내 + skip.

    분류 우선순위:
      1. **previously_imported** — `statement_import_log` 의 'inserted' /
         'matched_existing' 상태 row 가 같은 (entry_date, total_amount=
         row.amount, merchant=row.merchant) 로 존재. ledger 매칭보다 강함
         (사용자가 명시적으로 "이미 입력했음" 을 신호한 기록).
      2. **matched_existing** — ledger (현재 후잉 거래) 에 (date±N, amount)
         매칭. 종전 동작 그대로.
      3. **new** — 둘 다 매칭 안 된 신규 후보.

    `section_id` / `check_import_log` 는 caller (StatementImportScreen) 가
    open_ro 연결을 들고 있을 때만 의미 있는 — 매개변수로 분리해 단위 테스트
    (검사 off) 와 통합 흐름 (검사 on) 양쪽 깨끗하게.
    """
    from datetime import datetime

    def _parse(d: str) -> datetime | None:
        try:
            return datetime.strptime(d, "%Y%m%d")
        except (ValueError, TypeError):
            return None

    # 1) previously_imported — import_log 자연키 매칭.
    previously_imported: list[CSVRow] = []
    new_after_log: list[CSVRow] = []
    if check_import_log:
        try:
            from whooing_tui import data as tui_data
            with tui_data.open_ro() as conn:
                for r in rows:
                    hits = core_db.find_imports_by_natural_key(
                        conn,
                        entry_date=r.date,
                        total_amount=r.amount,
                        merchant=r.merchant,
                        section_id=section_id,
                    )
                    if hits:
                        previously_imported.append(r)
                    else:
                        new_after_log.append(r)
        except Exception:  # pragma: no cover — db 부재 시 silent fallback.
            log.debug("import_log dedup 실패 — silent fallback", exc_info=True)
            new_after_log = list(rows)
    else:
        new_after_log = list(rows)

    # 2) matched_existing — ledger 기반.
    matched: list[dict[str, Any]] = []
    new: list[CSVRow] = []
    used_ids: set[str] = set()
    for r in new_after_log:
        rdate = _parse(r.date)
        if not rdate:
            new.append(r)
            continue
        match = None
        for e in ledger:
            eid = str(e.get("entry_id"))
            if eid in used_ids:
                continue
            edate = _parse(str(e.get("entry_date") or ""))
            if not edate:
                continue
            if abs((edate - rdate).days) > tolerance_days:
                continue
            try:
                emoney = int(e.get("money") or 0)
            except (ValueError, TypeError):
                continue
            if emoney != r.amount:
                continue
            match = e
            used_ids.add(eid)
            break
        if match:
            matched.append({"row": r, "ledger": match})
        else:
            new.append(r)
    return matched, previously_imported, new


# ---- Main screen ----------------------------------------------------


class StatementImportScreen(ModalScreen[None]):
    """카드 명세서 import 화면 (CL #52841+ ModalScreen 으로 변경).

    종전엔 Screen 전체를 차지 — 사용자 요청으로 popup 모달 형태. 화면
    가운데 큰 frame, 뒷 화면 (EntriesScreen) 은 background 로 살짝 보임.
    진입 시 file_path 필수.
    """

    BINDINGS = [
        Binding("escape", "back", "뒤로"),
        Binding("ctrl+enter", "confirm", "입력 확정"),
    ]

    DEFAULT_CSS = """
    StatementImportScreen {
        align: center middle;
    }
    #import_frame {
        width: 95%;
        max-width: 160;
        min-width: 60;
        height: 90%;
        max-height: 50;
        min-height: 16;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
        layout: vertical;
    }
    #import_title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #status {
        height: auto;
        padding: 1 0;
        background: transparent;
    }
    #preview {
        height: 1fr;
    }
    #import_foot {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #pw_box {
        /* CL #51120+: 좁은 터미널 대응. */
        background: $panel;
        border: thick $primary;
        padding: 2;
        width: 95%;
        max-width: 50;
        min-width: 30;
        height: auto;
    }
    """

    def __init__(
        self,
        client: WhooingClient,
        section_id: str,
        r_account_id: str,
        file_path: str,
        card_label: str | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.section_id = section_id
        self.r_account_id = r_account_id
        self.file_path = file_path
        self.card_label = card_label
        self.kind: str | None = None
        self.issuer: str | None = None
        self.rows: list[CSVRow] = []
        self.matched: list[dict[str, Any]] = []
        self.previously_imported: list[CSVRow] = []   # CL #51129+: import_log dedup.
        self.proposals: list[CSVRow] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="import_frame"):
            yield Static("[bold]카드 명세서 import[/bold]", id="import_title")
            yield Static(
                f"명세서: {self.file_path}\n섹션: {self.section_id} · 카드: {self.r_account_id}",
                id="status",
            )
            yield DataTable(id="preview", zebra_stripes=True)
            yield Static(
                "Ctrl+Enter 입력 확정 · Esc 닫기",
                id="import_foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#preview", DataTable)
        table.add_columns("type", "date", "merchant", "amount", "note")
        self._kick_off_extract()

    @work(exclusive=True)
    async def _kick_off_extract(self) -> None:
        try:
            self.kind, self.issuer = _detect_format(self.file_path)
        except (FileNotFoundError, ValueError) as ex:
            self._set_status(f"❌ {ex}")
            return
        if not self.issuer:
            self._set_status(f"❌ {self.kind!r} 자동 detect 실패 — issuer 명시 필요.")
            return
        self._set_status(f"detect: kind={self.kind} issuer={self.issuer}")

        # password (HTML only)
        password: str | None = None
        if self.kind == "html":
            password = (
                os.getenv("WHOOING_CARD_HTML_PASSWORD")
                or os.getenv("WHOOING_HANACARD_PASSWORD")
                or ""
            ).strip()
            if not password:
                # ask via modal
                password = await self.app.push_screen_wait(
                    PasswordModal()
                )
                if not password:
                    self._set_status("password 미입력 — 중단.")
                    return

        # extract
        try:
            self.rows = await _extract_rows(
                self.file_path, self.kind, self.issuer, password,
            )
        except Exception as ex:
            self._set_status(f"❌ 어댑터 실패: {ex}")
            return
        if not self.rows:
            self._set_status("⚠️ 거래 0건 — 명세서 형식이 바뀌었을 가능성.")
            return

        # dedup
        # CL #52832+ crash audit: adapter 가 잘못된 date 문자열 (예: 빈문자
        # 열 / "20259999") 을 돌려주면 strptime 이 ValueError → worker
        # traceback. valid YYYYMMDD 8자리 digit 만 살림.
        dates = sorted({
            r.date for r in self.rows
            if isinstance(r.date, str) and len(r.date) == 8 and r.date.isdigit()
        })
        if not dates:
            self._set_status(
                "⚠️ 거래에 유효한 날짜 (YYYYMMDD) 가 없습니다 — 어댑터 확인.",
            )
            return
        from datetime import datetime, timedelta
        try:
            d0 = datetime.strptime(dates[0], "%Y%m%d") - timedelta(days=2)
            d1 = datetime.strptime(dates[-1], "%Y%m%d") + timedelta(days=2)
        except ValueError as ex:
            self._set_status(f"⚠️ 날짜 파싱 실패: {ex}")
            return
        ledger = await self.client.list_entries(
            section_id=self.section_id,
            start_date=d0.strftime("%Y%m%d"),
            end_date=d1.strftime("%Y%m%d"),
        )
        # CL #51129+: 3-tuple — (ledger 매칭, import_log 매칭, 신규).
        self.matched, self.previously_imported, self.proposals = _dedup(
            self.rows, ledger, section_id=self.section_id,
        )

        prev_part = (
            f" / {len(self.previously_imported)} 이미 import"
            if self.previously_imported else ""
        )
        self._set_status(
            f"{len(self.rows)} 건 추출 → {len(self.matched)} 기존 ledger"
            f"{prev_part} / {len(self.proposals)} 신규 (Ctrl+Enter 로 입력)"
        )
        self._populate_table()

    def _populate_table(self) -> None:
        table = self.query_one("#preview", DataTable)
        table.clear()
        for m in self.matched:
            r = m["row"]; e = m["ledger"]
            table.add_row(
                "matched", r.date, r.merchant[:30],
                f"{r.amount:,}", f"= entry {e.get('entry_id')}",
            )
        for r in self.previously_imported:
            # CL #51129+: import_log 매칭 — 이미 import 된 거래.
            table.add_row(
                "prev", r.date, r.merchant[:30],
                f"{r.amount:,}", "(이미 import 됨 — skip)",
            )
        for r in self.proposals:
            table.add_row(
                "new", r.date, r.merchant[:30],
                f"{r.amount:,}", "(신규)",
            )

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(
            f"명세서: {self.file_path}\n{text}"
        )

    @work(exclusive=True)
    async def action_confirm(self) -> None:
        if not self.proposals:
            self._set_status("입력할 신규 거래 없음.")
            return
        # accounts-list 로 r_account type 결정 (보통 liabilities)
        accounts = await self.client.list_accounts(self.section_id)
        r_type = _find_account_type(accounts, self.r_account_id) or "liabilities"
        l_default = "x50"  # 식비 fallback
        l_type = "expenses"

        period_start = self.proposals[0].date
        period_end = self.proposals[-1].date
        inserted = 0
        failed = 0
        for r in self.proposals:
            try:
                # Direct REST call — TUI 가 직접 entries CRUD (DESIGN.md 정책)
                resp = await self.client.create_entry(
                    section_id=self.section_id,
                    entry_date=r.date,
                    l_account=l_type, l_account_id=l_default,
                    r_account=r_type, r_account_id=self.r_account_id,
                    money=r.amount,
                    item=r.merchant[:60],
                    memo=f"TUI: {self.file_path[-40:]}",
                )
                eid = resp.get("entry_id") if isinstance(resp, dict) else None
                with tui_data.open_rw() as conn:
                    core_db.log_import(
                        conn,
                        source_file=self.file_path,
                        source_kind=self.kind or "html",
                        statement_period_start=period_start,
                        statement_period_end=period_end,
                        issuer=self.issuer or "unknown",
                        card_label=self.card_label,
                        entry_date=r.date,
                        merchant=r.merchant,
                        original_amount=r.amount,
                        fee_amount=0,
                        total_amount=r.amount,
                        currency="KRW",
                        foreign_amount=None, exchange_rate=None,
                        section_id=self.section_id,
                        l_account_id=l_default, r_account_id=self.r_account_id,
                        whooing_entry_id=str(eid) if eid else None,
                        status="inserted",
                        error_message=None, notes=None,
                    )
                inserted += 1
            except Exception as ex:
                failed += 1
                log.exception("entry insert failed: %s", ex)

        self._set_status(
            f"입력 완료: {inserted} 성공 / {failed} 실패. "
            f"({len(self.matched)} 건은 dedup 으로 skip)"
        )

    def action_back(self) -> None:
        # CL #52841+: ModalScreen 이므로 dismiss. pop_screen 도 동작하지만
        # dismiss 가 modal 의 표준 종료 path (callback 호출 등 포함).
        self.dismiss(None)


def _find_account_type(accounts: dict, account_id: str) -> str | None:
    """accounts dict ({type: [{account_id, ...}, ...]}) 에서 type 찾기."""
    for atype, alist in accounts.items():
        if not isinstance(alist, list):
            # 후잉 응답이 dict 인 경우도 있음 (nested by id)
            try:
                items = alist.values()
            except AttributeError:
                continue
        else:
            items = alist
        for a in items:
            if a.get("account_id") == account_id:
                return atype
    return None
