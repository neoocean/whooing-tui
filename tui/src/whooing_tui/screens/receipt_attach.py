"""ReceiptAttachScreen — PDF 영수증/인보이스 → 후잉 거래 자동 매칭/첨부.

CL #51128+ 사용자 요청:
> 여러 서비스로부터 오는 영수증, 인보이스 pdf를 읽어 이 파일에 해당하는
> 거래를 자동으로 찾아 첨부하는 기능을 만들어주세요. 만약 해당하는 거래가
> 아직 입력되지 않았다면 거래내역을 직접 제안하고 사용자의 조작에 의해
> 입력되게 해주세요.

흐름:
  1. on_mount → `core.receipt.extract_receipt(file_path)` 로 (date,
     amount, merchant) 추출.
  2. 후잉 entries (날짜 ±7일, amount 정확 일치) 조회 → 후보 list.
  3. 후보 N개 → DataTable. Cursor 로 선택, `a` 로 첨부 (단일 클릭).
  4. 후보 0개 → status 안내 + `n` 로 거래 제안 dialog (prefilled).
     dialog 저장 시 → 거래 생성 → 그 entry 에 PDF 첨부.

키:
  ↑/↓        후보 선택.
  a          선택 entry 에 PDF 첨부 + 종료.
  n          거래 제안 dialog (prefilled with extracted data).
  escape     취소 / 뒤로.
  r          재추출 (드물게 필요 — manual refresh).

첨부는 기존 `attachment_browser.add_attachment(...)` 를 호출 — 같은 sha
dedup / db row insert / P4 자동 submit 흐름 그대로 (CL #51123+, #51124+).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from whooing_core.receipt import ReceiptInfo, extract_receipt

from whooing_tui.client import WhooingClient
from whooing_tui.dates import today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


def _fmt_date_dashed(yyyymmdd: str | None) -> str:
    """YYYYMMDD → YYYY-MM-DD (display)."""
    if not yyyymmdd or len(str(yyyymmdd)) < 8:
        return str(yyyymmdd or "")
    s = str(yyyymmdd)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def find_candidate_entries(
    entries: list[dict[str, Any]],
    *,
    date: str | None,
    amount: int | None,
    window_days: int = 7,
) -> list[dict[str, Any]]:
    """후잉 entries 중 (date ±window_days, amount 정확 일치) 후보.

    date 가 None 이면 amount 만 일치 (윈도우 무시).
    amount 가 None 이면 빈 list (amount 없이는 매칭 불가).
    """
    if not entries or amount is None:
        return []
    target_amount = int(amount)
    target_date: datetime | None = None
    if date and len(date) == 8 and date.isdigit():
        try:
            target_date = datetime.strptime(date, "%Y%m%d")
        except ValueError:
            target_date = None

    out: list[dict[str, Any]] = []
    for e in entries:
        try:
            e_amount = int(e.get("money") or 0)
        except (TypeError, ValueError):
            continue
        if e_amount != target_amount:
            continue
        if target_date is not None:
            e_date_str = str(e.get("entry_date") or "").split(".", 1)[0][:8]
            if not (e_date_str and e_date_str.isdigit() and len(e_date_str) == 8):
                continue
            try:
                e_date = datetime.strptime(e_date_str, "%Y%m%d")
            except ValueError:
                continue
            if abs((e_date - target_date).days) > window_days:
                continue
        out.append(e)
    return out


class ReceiptAttachScreen(Screen):
    """PDF 영수증 → 매칭 거래 첨부 / 제안 화면."""

    BINDINGS = [
        Binding("escape", "back", "뒤로", show=True),
        *bind_ko("a", "attach_selected", "Attach", show=True, priority=True),
        *bind_ko("n", "propose_new", "New", show=True, priority=True),
        *bind_ko("r", "re_extract", "재추출", show=True),
    ]

    DEFAULT_CSS = """
    ReceiptAttachScreen {
        layout: vertical;
    }
    #ra_status {
        height: auto;
        padding: 1;
        background: $boost;
    }
    #ra_status.error { color: $error; }
    #ra_status.warn  { color: $warning; }
    """

    def __init__(
        self,
        client: WhooingClient,
        session: SessionState,
        file_path: str,
    ) -> None:
        super().__init__()
        self._client = client
        self._session = session
        self.file_path = file_path
        # 추출 결과 + 후보 — 테스트가 검사할 수 있도록 public.
        self.receipt: ReceiptInfo | None = None
        self.candidates: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"영수증: {self.file_path}", id="ra_status")
        yield Vertical(
            DataTable(id="ra_table", zebra_stripes=True, cursor_type="row"),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#ra_table", DataTable)
        table.add_columns("date", "money", "left", "right", "item", "memo")
        self._set_status("PDF 추출 중…")
        self._kick_off_extract()

    # ---- workers ---------------------------------------------------------

    @work(exclusive=True, group="receipt", name="extract")
    async def _kick_off_extract(self) -> None:
        # 추출 — pdfplumber 가 IO 라 별도 worker.
        try:
            info = extract_receipt(self.file_path)
        except Exception as ex:  # pragma: no cover — pdfplumber 내부 오류.
            log.exception("receipt extract failed")
            self._set_status(f"❌ 추출 실패: {ex}", error=True)
            return
        self.receipt = info
        # 후보 검색.
        if not self._session.section_id:
            self._set_status("활성 섹션 없음 — 후보 검색 불가.", error=True)
            return
        # 검색 윈도우 ±7일.
        win = 7
        date = info.date or today_yyyymmdd()
        try:
            d0 = (datetime.strptime(date, "%Y%m%d") - timedelta(days=win)).strftime("%Y%m%d")
            d1 = (datetime.strptime(date, "%Y%m%d") + timedelta(days=win)).strftime("%Y%m%d")
        except ValueError:
            d0, d1 = today_yyyymmdd(), today_yyyymmdd()
        try:
            entries = await self._client.list_entries(
                section_id=self._session.section_id,
                start_date=d0, end_date=d1,
            )
        except Exception as ex:
            log.exception("list_entries failed")
            self._set_status(f"❌ 거래 조회 실패: {ex}", error=True)
            return
        self.candidates = find_candidate_entries(
            entries, date=info.date, amount=info.amount, window_days=win,
        )
        self._populate_table()
        self._announce_extracted()

    # ---- UI 갱신 ---------------------------------------------------------

    def _populate_table(self) -> None:
        table = self.query_one("#ra_table", DataTable)
        table.clear()
        for e in self.candidates:
            l_id = e.get("l_account_id") or ""
            r_id = e.get("r_account_id") or ""
            table.add_row(
                _fmt_date_dashed(e.get("entry_date")),
                f"{int(e.get('money') or 0):,}",
                self._session.title_of(l_id) if l_id else "",
                self._session.title_of(r_id) if r_id else "",
                str(e.get("item") or "")[:30],
                str(e.get("memo") or "")[:20],
                key=str(e.get("entry_id") or ""),
            )

    def _announce_extracted(self) -> None:
        info = self.receipt
        if info is None:
            return
        meta_parts = []
        if info.date:
            meta_parts.append(f"date={_fmt_date_dashed(info.date)}")
        if info.amount is not None:
            meta_parts.append(f"amount={info.amount:,}")
        if info.merchant:
            meta_parts.append(f"merchant={info.merchant[:30]}")
        meta_parts.append(f"conf={info.confidence:.2f}")
        meta = ", ".join(meta_parts)
        n = len(self.candidates)
        if n == 0:
            self._set_status(
                f"추출: {meta}\n"
                f"매칭 거래 0건 — `n` 으로 거래 제안 / `r` 로 재추출.",
                warn=True,
            )
        elif n == 1:
            self._set_status(
                f"추출: {meta}\n"
                f"매칭 거래 1건 — `a` 로 첨부.",
            )
        else:
            self._set_status(
                f"추출: {meta}\n"
                f"매칭 거래 {n}건 — ↑/↓ 로 선택 후 `a` 로 첨부.",
            )

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        bar = self.query_one("#ra_status", Static)
        bar.update(f"영수증: {self.file_path}\n{text}")
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")

    # ---- actions ---------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_re_extract(self) -> None:
        self._set_status("재추출 중…")
        self._kick_off_extract()

    def action_attach_selected(self) -> None:
        """현재 cursor 의 entry 에 PDF 첨부.

        파일은 attachment_browser.add_attachment 가 sha dedup / db / P4
        자동 submit 까지 모두 처리.
        """
        from whooing_tui.screens import attachment_browser as ab
        if not self.candidates:
            self._set_status("선택할 거래가 없습니다 — `n` 으로 제안.", warn=True)
            return
        table = self.query_one("#ra_table", DataTable)
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            eid = str(row_key.value)
        except (AttributeError, TypeError, ValueError):
            self._set_status("선택된 거래를 회수하지 못함.", error=True)
            return
        if not eid:
            self._set_status("entry_id 비어있음 — 첨부 불가.", error=True)
            return
        try:
            row = ab.add_attachment(
                eid, self.file_path, section_id=self._session.section_id,
            )
        except (FileNotFoundError, ValueError) as ex:
            self._set_status(f"첨부 실패: {ex}", error=True)
            return
        self.notify(f"첨부 완료 — entry {eid} (attach id={row.get('id')})")
        self.app.pop_screen()

    def action_propose_new(self) -> None:
        """추출된 (date, amount, merchant) 로 EntryEditDialog 를 prefill —
        사용자 저장 시 거래 생성 + 새 entry 에 PDF 첨부.
        """
        self._propose_new_worker()

    @work(exclusive=True, group="receipt", name="propose_new")
    async def _propose_new_worker(self) -> None:
        from whooing_tui.screens.edit_entry import EntryDraft, EntryEditDialog
        from whooing_tui.screens import attachment_browser as ab
        from whooing_tui.models import ToolError

        info = self.receipt
        if info is None:
            self._set_status("추출 결과가 아직 준비되지 않음.", warn=True)
            return
        # EntryEditDialog 의 existing prefill 형식 (entries.action_new_entry 패턴).
        prefill: dict[str, Any] = {}
        if info.date:
            prefill["entry_date"] = info.date
        if info.amount is not None:
            prefill["money"] = info.amount
        if info.merchant:
            prefill["item"] = info.merchant[:60]
        # 메모: 영수증 파일명 — 사용자가 dialog 에서 자유 수정 가능.
        prefill["memo"] = f"영수증: {info.source_file.split('/')[-1]}"

        dialog = EntryEditDialog(self._session, existing=prefill)
        draft: EntryDraft | None = await self.app.push_screen_wait(dialog)
        if draft is None:
            self._set_status("거래 제안 취소.", warn=True)
            return
        # 후잉 거래 생성 — entries 의 _submit_create 를 모방하되 본 worker 가 직접.
        l_type = draft.l_type
        r_type = draft.r_type
        if not l_type or not r_type:
            self._set_status(
                "계정 type 누락 — 다시 시도하세요.", error=True,
            )
            return
        try:
            response = await self._client.create_entry(
                section_id=self._session.section_id,
                l_account=l_type,
                l_account_id=draft.l_account_id,
                r_account=r_type,
                r_account_id=draft.r_account_id,
                money=draft.money,
                item=draft.item,
                memo=draft.memo,
                entry_date=draft.entry_date,
            )
        except ToolError as e:
            self._set_status(f"거래 생성 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("create_entry failed in propose_new")
            self._set_status(f"거래 생성 실패: {e}", error=True)
            return
        # 새 entry_id 회수 후 PDF 첨부.
        new_eid: str | None = None
        if isinstance(response, dict):
            new_eid = (
                response.get("entry_id")
                or (response.get("entries") or [{}])[0].get("entry_id")
                or (response.get("results") or [{}])[0].get("entry_id")
            )
        if not new_eid:
            self._set_status(
                "거래 생성 OK 였으나 entry_id 회수 실패 — 첨부는 화면에서 직접.",
                warn=True,
            )
            return
        # CL #51155+ (review C2): EntriesScreen._submit_create 와 동일하게
        # 로컬 sqlite (entry_annotations / entry_hashtags) 에도 mirror.
        # 종전엔 후잉에만 가고 로컬 검색/통계 인덱스에서 누락됐다.
        if draft.memo or draft.tags:
            self._persist_local_for_new_entry(
                entry_id=str(new_eid),
                section_id=self._session.section_id,
                memo=draft.memo,
                tags=draft.tags,
            )
        try:
            ab.add_attachment(
                str(new_eid), self.file_path,
                section_id=self._session.section_id,
            )
        except (FileNotFoundError, ValueError) as ex:
            self._set_status(
                f"거래 생성 OK / 첨부 실패: {ex}", error=True,
            )
            return
        self.notify(f"거래 생성 + 영수증 첨부 — entry {new_eid}")
        self.app.pop_screen()

    def _persist_local_for_new_entry(
        self, *, entry_id: str, section_id: str,
        memo: str, tags: list[str],
    ) -> None:
        """CL #51155+ (review C2): EntriesScreen._persist_local 의 축약본 —
        receipt 흐름에서도 로컬 sqlite mirror + P4 자동 submit. 실패 silent.
        """
        try:
            from whooing_core import db as core_db
            from whooing_tui import data as tui_data
            with tui_data.open_rw() as conn:
                prev = core_db.get_annotations_for(conn, [entry_id])
                previous_tags = list(
                    prev.get(entry_id, {}).get("hashtags", []) or []
                )
                core_db.upsert_annotation(
                    conn, entry_id=entry_id,
                    section_id=section_id or None, note=memo or None,
                )
                core_db.set_hashtags(
                    conn, entry_id, list(tags or []),
                    section_id=section_id or None,
                )
            from whooing_tui import p4_sync
            p4_sync.submit_db_to_p4(
                tui_data.db_path(),
                p4_sync.describe_annotation(
                    entry_id=entry_id,
                    memo_changed=bool(memo),
                    tags=list(tags or []),
                    previous_tags=previous_tags,
                ),
            )
        except Exception:  # pragma: no cover — 로컬 mirror 실패는 silent
            log.exception("receipt_attach: persist_local failed")
