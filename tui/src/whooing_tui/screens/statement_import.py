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


# CL #52910+: 일괄 import 의 실패 메시지를 사용자 가시 modal + 파일로
# 보존. log.exception 만 호출하면 stderr 로 가서 TUI 화면에 깜빡이고
# 사라짐 — 사용자 보고: "에러메시지들이 지나가고 사라집니다".

class _ErrorReportModal(ModalScreen[None]):
    """일괄 import 실패 메시지를 보여주는 modal — 닫기 + log 파일 경로 안내.

    터미널의 mouse 선택 (대부분 환경에서 Shift/Option+drag) 으로 텍스트
    선택 가능. 그게 안 되는 환경에서는 status 의 log 파일 경로를 cat /
    less 로 확인.
    """

    DEFAULT_CSS = """
    _ErrorReportModal { align: center middle; }
    #err-frame {
        width: 95%;
        max-width: 140;
        min-width: 60;
        height: 90%;
        max-height: 40;
        min-height: 16;
        padding: 1 2;
        border: thick $error;
        background: $surface;
        layout: vertical;
    }
    #err-title {
        height: 1;
        content-align: center middle;
        color: $error;
    }
    #err-summary {
        height: auto;
        padding: 1 0;
        color: $text-muted;
    }
    #err-body {
        height: 1fr;
        border: solid $error;
        padding: 0 1;
    }
    #err-foot {
        height: 3;
        align: center middle;
    }
    #err-foot Button { min-width: 20; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("enter", "close", "", show=False),
    ]

    def __init__(
        self,
        *,
        title: str,
        summary: str,
        body: str,
        log_path: str | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._summary = summary
        self._body = body
        self._log_path = log_path

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll
        with Vertical(id="err-frame"):
            yield Static(f"[bold]{self._title}[/bold]", id="err-title")
            summary = self._summary
            if self._log_path:
                summary += f"\n[dim]전체 로그: {self._log_path}[/dim]"
            yield Static(summary, id="err-summary")
            with VerticalScroll(id="err-body"):
                yield Static(self._body)
            with Horizontal(id="err-foot"):
                yield Button("닫기 (Esc)", id="err-close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "err-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


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

    # 1) previously_imported — import_log 매칭.
    # CL #52917+: 종전 strict natural key (merchant 정확 일치) + 새 fuzzy
    # pass (date + amount 만 매칭 + merchant 정규화 / 부분 일치 확인).
    # "스타벅스" ↔ "스타벅스 강남점" 같은 표기 변형도 dup 으로 잡힌다.
    previously_imported: list[CSVRow] = []
    new_after_log: list[CSVRow] = []
    if check_import_log:
        try:
            from whooing_core.dupes import merchant_similar
            from whooing_tui import data as tui_data
            with tui_data.open_ro() as conn:
                for r in rows:
                    # Strict 먼저 — 정확 일치면 즉시 dup.
                    hits = core_db.find_imports_by_natural_key(
                        conn,
                        entry_date=r.date,
                        total_amount=r.amount,
                        merchant=r.merchant,
                        section_id=section_id,
                    )
                    if not hits:
                        # Fuzzy — (date, amount) 만으로 후보 가져와 merchant
                        # 정규화 / 부분 일치 검사.
                        candidates = core_db.find_imports_by_date_amount(
                            conn,
                            entry_date=r.date,
                            total_amount=r.amount,
                            section_id=section_id,
                        )
                        hits = [
                            c for c in candidates
                            if merchant_similar(r.merchant, c.get("merchant") or "")
                        ]
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
    # CL #52917+: 후보가 여러 개일 때 *merchant 유사* 한 ledger entry 를 우선
    # 채택 (tiebreaker). 종전엔 첫 매칭 ledger entry 를 그대로 잡아 같은
    # 날 같은 금액의 다른 거래에 잘못 묶일 수 있었음.
    from whooing_core.dupes import merchant_similar
    matched: list[dict[str, Any]] = []
    new: list[CSVRow] = []
    used_ids: set[str] = set()
    for r in new_after_log:
        rdate = _parse(r.date)
        if not rdate:
            new.append(r)
            continue
        # 1차 통과: date / amount 조건 만족하는 후보 모두 수집.
        candidates: list[dict[str, Any]] = []
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
            candidates.append(e)
        # 2차 우선순위: merchant 유사한 ledger entry > 그 외 첫 후보.
        match = None
        for e in candidates:
            if merchant_similar(r.merchant, e.get("item") or ""):
                match = e
                break
        if match is None and candidates:
            match = candidates[0]
        if match:
            matched.append({"row": r, "ledger": match})
            used_ids.add(str(match.get("entry_id")))
        else:
            new.append(r)

    # 3) within-batch dedup — 같은 명세서 안에서 (date + amount + 정규화
    # merchant) 동일한 row 가 2회 이상이면 첫 1건만 남기고 나머지는
    # previously_imported 로 분류.
    # CL #52917+: HTML 명세서가 다중 페이지로 같은 거래를 두 번 리포트하는
    # 케이스, 또는 어댑터의 버그로 같은 row 가 중복 추출되는 케이스 방어.
    from whooing_core.dupes import normalize_text
    seen: set[tuple[str, int, str]] = set()
    dedup_new: list[CSVRow] = []
    for r in new:
        key = (r.date, r.amount, normalize_text(r.merchant))
        if key in seen:
            previously_imported.append(r)
        else:
            seen.add(key)
            dedup_new.append(r)
    new = dedup_new
    return matched, previously_imported, new


def _compute_suspect_map(
    proposals: list[CSVRow],
    ledger: list[dict[str, Any]],
) -> dict[int, str]:
    """CL #52912+: strict dedup 을 통과한 proposal 중 *fuzzy* 의심 매칭.

    proposal 은 정의상 `_dedup` 의 ledger ±2일 strict 매칭을 통과한 것.
    그래도 다음 케이스는 잠재적 중복:
      - 같은 금액 + 날짜 3~7일 차 (지연된 카드 처리).
      - 비슷한 금액 (±1%) + 날짜 ±2일 (수수료 / 환율 차).

    return: `{proposal_index: reason_text}`. 매칭 없는 proposal 은 key 미포함.
    fuzzy 매칭은 사용자에게 안내일 뿐 자동 skip 하지 않는다 — UI 가 의심
    표시만 보이고 사용자가 space 로 deselect 가능.
    """
    from datetime import datetime

    def _parse(d: str) -> datetime | None:
        try:
            return datetime.strptime(d, "%Y%m%d")
        except (ValueError, TypeError):
            return None

    # CL #52917+: 같은 가맹점 (merchant 유사) + 가까운 날짜의 ledger 매칭도
    # 의심 신호로 — 금액이 약간 달라도 같은 거래일 가능성.
    from whooing_core.dupes import merchant_similar

    suspect: dict[int, str] = {}
    for idx, r in enumerate(proposals):
        rdate = _parse(r.date)
        if not rdate:
            continue
        for e in ledger:
            edate = _parse(str(e.get("entry_date") or ""))
            if not edate:
                continue
            day_diff = abs((edate - rdate).days)
            try:
                emoney = int(e.get("money") or 0)
            except (ValueError, TypeError):
                continue
            eid = str(e.get("entry_id") or "?")
            # 1) 같은 금액 + 3~7일 차이 — 가맹점 처리 지연 가능.
            if emoney == r.amount and 2 < day_diff <= 7:
                suspect[idx] = (
                    f"같은 금액, {day_diff}일 차 ledger {eid}"
                )
                break
            # 2) 금액 ±1% (수수료 / 환율) + 날짜 ±2일.
            if (
                day_diff <= 2 and r.amount > 0
                and abs(emoney - r.amount) / r.amount <= 0.01
                and emoney != r.amount
            ):
                suspect[idx] = (
                    f"금액 유사 ({emoney:,}) {day_diff}일 차 ledger {eid}"
                )
                break
            # 3) CL #52917+: merchant 유사 + 날짜 ±7일 + 금액 ±10%
            #    (같은 가맹점 정기 결제 / 가맹점 환불 후 재결제 등).
            if (
                day_diff <= 7 and r.amount > 0
                and abs(emoney - r.amount) / r.amount <= 0.10
                and emoney != r.amount
                and merchant_similar(r.merchant, e.get("item") or "")
            ):
                suspect[idx] = (
                    f"가맹점 유사 ({e.get('item') or '?'} / {emoney:,}) "
                    f"{day_diff}일 차 ledger {eid}"
                )
                break
    return suspect


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
        # CL #52912+: 선택 토글 / 일괄 선택 / 일괄 해제 / 의심만 해제.
        Binding("space", "toggle_select", "선택 토글", show=True, priority=True),
        Binding("ctrl+a", "select_all", "전체 선택", show=True),
        Binding("ctrl+d", "deselect_all", "전체 해제", show=True),
        Binding("ctrl+u", "deselect_suspect", "의심만 해제", show=True),
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
        # CL #52912+: 사용자 요청 — proposal 별 선택 상태 + 의심 표시.
        # `_selected[i]` 가 True 면 Ctrl+Enter 시 입력. 기본 True (의심도
        # 자동 선택, 사용자가 보고 명시 deselect). `_suspect[i]` 는 fuzzy
        # 매칭으로 ledger 와 유사한 거래가 있을 때의 안내 문구.
        self._selected: list[bool] = []
        self._suspect: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="import_frame"):
            yield Static("[bold]카드 명세서 import[/bold]", id="import_title")
            yield Static(
                f"명세서: {self.file_path}\n섹션: {self.section_id} · 카드: {self.r_account_id}",
                id="status",
            )
            yield DataTable(id="preview", zebra_stripes=True)
            yield Static(
                "Space 선택 토글 · Ctrl+A 전체 · Ctrl+D 해제 · Ctrl+U 의심 해제 · "
                "Ctrl+Enter 입력 · Esc 닫기",
                id="import_foot",
            )

    def on_mount(self) -> None:
        table = self.query_one("#preview", DataTable)
        # CL #52912+: 맨 앞 컬럼 "✓" = proposal 의 선택 상태. matched/prev
        # 행은 항상 비어있음 (skip 대상).
        table.add_columns("✓", "type", "date", "merchant", "amount", "note")
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
        # CL #52912+: fuzzy 의심 매칭을 위해 ledger 윈도우를 ±7일로 확장.
        # 종전 ±2일 (strict dedup 용) 보다 넓게 — 같은 거래가 가맹점 처리
        # 지연 등으로 며칠 차이로 들어왔는지 fuzzy 검사.
        d0w = d0 - timedelta(days=5)
        d1w = d1 + timedelta(days=5)
        ledger = await self.client.list_entries(
            section_id=self.section_id,
            start_date=d0w.strftime("%Y%m%d"),
            end_date=d1w.strftime("%Y%m%d"),
        )
        # CL #51129+: 3-tuple — (ledger 매칭, import_log 매칭, 신규).
        self.matched, self.previously_imported, self.proposals = _dedup(
            self.rows, ledger, section_id=self.section_id,
        )

        # CL #52912+: proposals (strict dedup 통과) 중 fuzzy 의심 케이스를
        # 사용자에게 표시. Strict 통과 = 같은 날 ±2일 + 정확 금액 매칭 없음.
        # Fuzzy 검사:
        #   - 같은 금액 + 날짜 3~7일 차 (지연된 카드 처리 가능).
        #   - 금액 ±1% + 날짜 ±2일 + 같은 일자에 비슷한 거래 (수수료/환율).
        self._suspect = _compute_suspect_map(self.proposals, ledger)
        # 모든 proposal 을 default selected — 사용자가 의심 항목을 보고
        # 명시 deselect 하도록.
        self._selected = [True] * len(self.proposals)

        prev_part = (
            f" / {len(self.previously_imported)} 이미 import"
            if self.previously_imported else ""
        )
        suspect_part = (
            f" / {len(self._suspect)} 의심 ⚠️" if self._suspect else ""
        )
        self._set_status(
            f"{len(self.rows)} 건 추출 → {len(self.matched)} 기존 ledger"
            f"{prev_part} / {len(self.proposals)} 신규{suspect_part} "
            f"(Space 선택 토글 · Ctrl+Enter 입력)"
        )
        self._populate_table()

    def _populate_table(self) -> None:
        # CL #52912+: 선택 표시 (✓) + 의심 매칭 표기. proposal row 는
        # key 로 `prop:<idx>` 부여 — space 핸들러가 row → index 매핑.
        table = self.query_one("#preview", DataTable)
        table.clear()
        for m in self.matched:
            r = m["row"]; e = m["ledger"]
            table.add_row(
                " ", "matched", r.date, r.merchant[:30],
                f"{r.amount:,}", f"= entry {e.get('entry_id')}",
            )
        for r in self.previously_imported:
            table.add_row(
                " ", "prev", r.date, r.merchant[:30],
                f"{r.amount:,}", "(이미 import 됨 — skip)",
            )
        for idx, r in enumerate(self.proposals):
            mark = "✓" if (idx < len(self._selected) and self._selected[idx]) else " "
            suspect_note = self._suspect.get(idx)
            if suspect_note:
                note = f"⚠️ 의심: {suspect_note}"
            else:
                note = "(신규)"
            table.add_row(
                mark, "new", r.date, r.merchant[:30],
                f"{r.amount:,}", note,
                key=f"prop:{idx}",
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
        # CL #52912+: 사용자가 Space 로 선택 / 해제 한 proposal 만 import.
        selected = [
            r for i, r in enumerate(self.proposals)
            if i < len(self._selected) and self._selected[i]
        ]
        if not selected:
            self._set_status(
                "선택된 항목이 없습니다 — Space 로 1건 이상 선택하세요.",
            )
            return
        skipped = len(self.proposals) - len(selected)
        # accounts-list 로 r_account type 결정 (보통 liabilities)
        accounts = await self.client.list_accounts(self.section_id)
        r_type = _find_account_type(accounts, self.r_account_id) or "liabilities"
        l_default = "x50"  # 식비 fallback
        l_type = "expenses"

        period_start = selected[0].date
        period_end = selected[-1].date
        inserted = 0
        failed = 0
        # CL #52910+: 사용자 가시 에러 모음 — log.exception 만 호출하면 stderr
        # 로 가서 TUI 화면 뒤로 깜빡이고 사라진다 (사용자 보고). 모든 실패
        # 메시지를 모아 import 끝난 뒤 modal + 파일로 보여준다.
        error_lines: list[str] = []
        for idx, r in enumerate(selected, 1):
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
                # 사용자 가시 한 줄 + Python 로그 양쪽.
                error_lines.append(
                    f"[{idx}/{len(self.proposals)}] "
                    f"{r.date} {r.merchant[:30]} {r.amount:,} → "
                    f"{type(ex).__name__}: {ex}"
                )
                log.exception("entry insert failed: %s", ex)

        # 결과 status — 항상 표시. CL #52912+ 부터 skipped 카운트도 포함.
        skip_extra = (
            f" / 미선택 {skipped} 건 skip" if skipped else ""
        )
        self._set_status(
            f"입력 완료: {inserted} 성공 / {failed} 실패. "
            f"({len(self.matched)} 건 ledger dedup{skip_extra})"
        )

        # CL #52910+: 실패 있으면 modal + log 파일.
        if error_lines:
            log_path = self._write_error_log(error_lines)
            self._set_status(
                f"입력 완료: {inserted} 성공 / {failed} 실패 — "
                f"자세한 내용: {log_path}"
            )
            self.app.push_screen(_ErrorReportModal(
                title=f"카드 명세서 import — {failed}건 실패",
                summary=(
                    f"선택된 {len(selected)} 건 중 {inserted} 건 성공, "
                    f"{failed} 건 실패. 아래는 실패 내역 (mouse 로 선택 가능)."
                ),
                body="\n".join(error_lines),
                log_path=str(log_path),
            ))

    def _write_error_log(self, lines: list[str]) -> Path:
        """import 실패 내역을 timestamp 가 붙은 임시 파일로 — 사용자가 cat
        / less 로 열어 전체 내용을 확인 / 복사 가능.

        파일 형식: 한 줄에 한 실패. 헤더로 명세서 파일 + 시간 표기.
        """
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = Path("/tmp") / f"whooing-import-errors-{ts}.log"
        try:
            header = (
                f"# whooing-tui statement import errors\n"
                f"# statement: {self.file_path}\n"
                f"# at: {datetime.now().isoformat()}\n"
                f"# issuer: {self.issuer or 'unknown'}\n"
                f"# r_account_id: {self.r_account_id}\n"
                f"# failed: {len(lines)}\n"
                f"\n"
            )
            log_path.write_text(header + "\n".join(lines) + "\n")
        except Exception:  # pragma: no cover — 파일 쓰기 실패도 silent.
            log.exception("write error log failed")
        return log_path

    def action_back(self) -> None:
        # CL #52841+: ModalScreen 이므로 dismiss. pop_screen 도 동작하지만
        # dismiss 가 modal 의 표준 종료 path (callback 호출 등 포함).
        self.dismiss(None)

    # ---- CL #52912+ : 선택 토글 / 일괄 선택 ----------------------------------

    def _cursor_proposal_idx(self) -> int | None:
        """현재 cursor row 가 proposal 이면 그 index, 아니면 None."""
        try:
            table = self.query_one("#preview", DataTable)
            row = table.cursor_row
            if row is None or row < 0:
                return None
            key = table.coordinate_to_cell_key((row, 0)).row_key.value
        except Exception:  # pragma: no cover
            return None
        if not key or not str(key).startswith("prop:"):
            return None
        try:
            return int(str(key)[len("prop:"):])
        except ValueError:  # pragma: no cover
            return None

    def _refresh_select_marks(self) -> None:
        """`#preview` table 의 ✓ 컬럼만 갱신 — 전체 row 재렌더 X."""
        try:
            table = self.query_one("#preview", DataTable)
        except Exception:  # pragma: no cover
            return
        # proposal row 들은 matched / prev 다음에 위치. row index 계산.
        first_proposal_row = len(self.matched) + len(self.previously_imported)
        for idx in range(len(self.proposals)):
            row = first_proposal_row + idx
            mark = "✓" if self._selected[idx] else " "
            try:
                table.update_cell_at((row, 0), mark)
            except Exception:  # pragma: no cover — row 가 cleared 상태 등.
                pass
        # status 의 선택 수 갱신.
        sel = sum(1 for s in self._selected if s)
        self._set_status(
            f"{len(self.rows)} 건 추출 → {len(self.matched)} 기존 ledger / "
            f"{len(self.previously_imported)} 이미 import / "
            f"{len(self.proposals)} 신규 (선택 {sel}/{len(self.proposals)}) "
            f"· Space 토글 · Ctrl+Enter 입력"
        )

    def action_toggle_select(self) -> None:
        idx = self._cursor_proposal_idx()
        if idx is None:
            # proposal 이 아닌 row (matched/prev) — 사용자 안내.
            self._set_status(
                "선택 가능한 항목 (신규) 위에서 Space — matched / prev 는 항상 skip.",
            )
            return
        self._selected[idx] = not self._selected[idx]
        self._refresh_select_marks()

    def action_select_all(self) -> None:
        self._selected = [True] * len(self.proposals)
        self._refresh_select_marks()

    def action_deselect_all(self) -> None:
        self._selected = [False] * len(self.proposals)
        self._refresh_select_marks()

    def action_deselect_suspect(self) -> None:
        """⚠️ 의심 표시된 row 만 일괄 deselect — 자주 쓰는 단축."""
        for i in self._suspect.keys():
            if 0 <= i < len(self._selected):
                self._selected[i] = False
        self._refresh_select_marks()


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
