"""EntriesScreen — TUI 의 **초기 화면** (CL #51023+).

앱이 시작되면 본 화면이 첫 push 된다. 진입 시점에 자체적으로 sections /
accounts / entries 를 chain 으로 부팅한다 (별도 HomeScreen 없이):

  1. SessionState.section_id 가 비어있으면 sections-list 호출 → WHOOING_
     SECTION_ID 환경변수 우선, 없으면 첫 섹션을 자동 활성화.
  2. SessionState.accounts_flat 이 비어있으면 accounts-list 호출 → 양방향
     인덱스 빌드.
  3. 최근 N일 (기본 `config.entries.default_window_days`, 30) 거래 fetch.

100-cap pagination 위험은 footer 의 `.warn` 클래스로 인지 메시지를 노출.

별도 옵션 화면 (CL #51023):
  s   SectionPickerScreen 으로 push — 섹션 선택 후 dismiss → 자동 재부팅.
  a   AccountsScreen 으로 push — 계정과목 조회 / 추가 / 수정 / 삭제.
      돌아온 후 자동으로 entries 재로드 (cache 가 invalidate 됐을 수 있음).

키 바인딩 (Footer 가 표시):
  q / escape       앱 종료 (initial screen 이므로 pop 이 아닌 exit).
  r                재로드 (현재 윈도우, 캐시 강제 invalidate).
  + / -            윈도우 ±7일.
  s / a            섹션 picker / 계정과목 화면 push.
  n / Enter / d    거래 추가 / 수정 / 삭제 (EntryEditDialog / Confirm).
  ?                화면 도움말 (HelpModal).

DataTable 컬럼:
  date  money  left  right  item  memo

money 는 천단위 콤마. left/right 는 account_id 를 SessionState 의 양방향
인덱스로 즉시 title 로 변환 — 사용자에게는 코드 대신 이름이 보인다.
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from whooing_tui.client import WhooingClient
from whooing_tui.config import load_config
from whooing_tui.dates import days_ago_yyyymmdd, today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.screens.edit_entry import (
    ConfirmModal, EntryDraft, EntryEditDialog,
)
from whooing_tui.state import (
    default_section_id_from_env,
    load_last_section_id,
    save_last_section_id,
)

log = logging.getLogger(__name__)


def _fmt_money(v: Any) -> str:
    """후잉 money 는 정수 (KRW). 천단위 콤마 + 음수 부호 보존."""
    if v is None or v == "":
        return ""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:,}"


class EntriesScreen(Screen):
    """활성 섹션의 거래내역 화면."""

    DEFAULT_CSS = """
    EntriesScreen {
        layers: base;
    }
    #entries-body {
        height: 1fr;
    }
    #entries-table {
        height: 1fr;
        border: round $accent;
    }
    #status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #status.error {
        color: $error;
    }
    #status.warn {
        color: $warning;
    }
    """

    # 영문 letter key 는 한글 IME 일 때도 동작하도록 `bind_ko` 로 자모
    # binding 을 같이 등록 (CL #51041). escape / enter / question_mark /
    # plus / minus / equals_sign 은 IME 영향이 없으므로 그대로.
    BINDINGS = [
        *bind_ko("q", "back", "Quit", show=True),
        Binding("escape", "back", "Quit", show=False),
        *bind_ko("s", "open_sections", "Sections", show=True, priority=True),
        *bind_ko("a", "open_accounts", "Accounts", show=True, priority=True),
        *bind_ko("n", "new_entry", "New", show=True, priority=True),
        Binding("enter", "edit_entry", "Edit", show=True, priority=True),
        *bind_ko("d", "delete_entry", "Delete", show=True, priority=True),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        Binding("question_mark", "help", "Help", show=True, priority=True, key_display="?"),
        Binding("plus", "extend_window", "+7d", show=True),
        Binding("minus", "shrink_window", "-7d", show=True),
        Binding("equals_sign", "extend_window", "", show=False),  # '+' 키 (no shift)
    ]

    # 한 번에 fetch 할 거래는 후잉 server-side hard cap 100건. 단일 일자에
    # 100건 초과는 _list_entries_chunked 가 더 분할할 수 없으므로 footer
    # 에 경고 띄움 (DESIGN §4.3 + MEMORY §7).
    _SERVER_PAGE_CAP = 100

    def __init__(self, client: WhooingClient) -> None:
        super().__init__()
        self._client = client
        cfg = load_config()
        self._window_days: int = max(1, cfg.default_window_days)
        # status 평문 보관 (테스트 친화 — HomeScreen 과 동일 컨벤션)
        self.last_status: str = ""
        # 마지막 fetch 결과 메타 (테스트가 검사할 수 있도록)
        self.last_entry_count: int = 0
        self.last_cap_warning: bool = False
        # 표시 중인 entries — DataTable row index ↔ entry dict 1:1 매핑.
        # 사용자가 row 를 선택하면 entry_id / 기존 값을 dialog 로 prefill
        # 할 수 있도록.
        self._entries: list[dict[str, Any]] = []

    # ---- compose -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="entries-body"):
            yield DataTable(id="entries-table", zebra_stripes=True, cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    # ---- mount ---------------------------------------------------------

    def on_mount(self) -> None:
        table = self.query_one("#entries-table", DataTable)
        table.add_columns("date", "money", "left", "right", "item", "memo")
        self.set_status("거래내역 로드 중…")
        self.refresh_entries()
        table.focus()

    # ---- actions -------------------------------------------------------

    def action_back(self) -> None:
        """초기 화면이라 pop 대신 앱 종료."""
        self.app.exit()

    def action_help(self) -> None:
        """현재 화면의 BINDINGS 를 모달로 보여줌."""
        from whooing_tui.screens.help import HelpModal
        self.app.push_screen(HelpModal("EntriesScreen", list(self.BINDINGS)))

    def action_open_sections(self) -> None:
        """섹션 picker 모달 push. 사용자가 다른 섹션을 고르면 자동 재로드."""
        from whooing_tui.screens.sections import SectionPickerScreen
        session = self.app.session  # type: ignore[attr-defined]

        def _on_close(result: tuple[str, str | None] | None) -> None:
            if result is None:
                return
            sid, title = result
            if sid == session.section_id:
                # 같은 섹션 — 그대로 둔다.
                return
            session.set_section(sid, title)
            # 사용자 명시 선택은 영구 저장 — 다음 부팅 시 복원.
            save_last_section_id(sid)
            self.set_status(
                f"섹션 {sid} ({title or '?'}) 으로 전환. 재로드 중…",
            )
            self.refresh_entries()

        self.app.push_screen(
            SectionPickerScreen(
                self._client, current_section_id=session.section_id,
            ),
            _on_close,
        )

    def action_open_accounts(self) -> None:
        """계정과목 화면 push. 돌아온 후 자동으로 entries 재로드.

        AccountsScreen 안의 mutation 이 cache 를 invalidate 하므로 단순히
        refresh_entries() 만 부르면 fresh 한 결과가 나온다.
        """
        from whooing_tui.screens.accounts import AccountsScreen
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다 — `s` 로 먼저 선택하세요.", error=True)
            return

        def _on_close(_: Any) -> None:
            self.set_status("계정과목 화면에서 돌아왔습니다. 재로드 중…")
            self.refresh_entries()

        self.app.push_screen(AccountsScreen(self._client), _on_close)

    def action_refresh(self) -> None:
        # 사용자가 'r' = "지금 즉시 후잉 데이터" — 캐시가 있으면 invalidate.
        session = self.app.session  # type: ignore[attr-defined]
        invalidate = getattr(self._client, "invalidate_section", None)
        if session.section_id and callable(invalidate):
            invalidate(session.section_id)
        self.set_status("재로드 중…")
        self.refresh_entries()

    def action_extend_window(self) -> None:
        self._window_days = min(365 * 5, self._window_days + 7)
        self.set_status(f"윈도우 +7일 → 최근 {self._window_days}일. 재로드 중…")
        self.refresh_entries()

    def action_shrink_window(self) -> None:
        self._window_days = max(1, self._window_days - 7)
        self.set_status(f"윈도우 -7일 → 최근 {self._window_days}일. 재로드 중…")
        self.refresh_entries()

    # ---- new / edit / delete -----------------------------------------

    def _selected_entry(self) -> dict[str, Any] | None:
        """현재 DataTable cursor 가 가리키는 entry. 없으면 None."""
        if not self._entries:
            return None
        table = self.query_one("#entries-table", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._entries):
            return None
        return self._entries[row]

    def action_new_entry(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id or not session.accounts_flat:
            self.set_status("계정과목 캐시가 비어있습니다 — 홈에서 섹션을 다시 활성화하세요.", error=True)
            return

        def _on_close(draft: EntryDraft | None) -> None:
            if draft is None:
                self.set_status("입력 취소됨.")
                return
            self._submit_create(draft)

        self.app.push_screen(EntryEditDialog(session), _on_close)

    def action_edit_entry(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        if not target.get("entry_id"):
            self.set_status("이 거래에는 entry_id 가 없습니다 — 수정 불가.", error=True)
            return

        def _on_close(draft: EntryDraft | None) -> None:
            if draft is None:
                self.set_status("수정 취소됨.")
                return
            self._submit_update(draft)

        self.app.push_screen(EntryEditDialog(session, existing=target), _on_close)

    def action_delete_entry(self) -> None:
        target = self._selected_entry()
        if target is None:
            self.set_status("선택된 거래가 없습니다.", error=True)
            return
        eid = target.get("entry_id")
        if not eid:
            self.set_status("이 거래에는 entry_id 가 없습니다 — 삭제 불가.", error=True)
            return

        # 사용자에게 거래 요약을 보여주고 y 로 확정.
        session = self.app.session  # type: ignore[attr-defined]
        l_name = session.title_of(target.get("l_account_id") or "")
        r_name = session.title_of(target.get("r_account_id") or "")
        msg = (
            f"이 거래를 영구 삭제할까요?\n\n"
            f"  date  : {target.get('entry_date') or ''}\n"
            f"  money : {_fmt_money(target.get('money'))}\n"
            f"  left  : {l_name}\n"
            f"  right : {r_name}\n"
            f"  item  : {target.get('item') or ''}\n\n"
            f"되돌릴 수 없습니다."
        )

        def _on_close(yes: bool | None) -> None:
            # ConfirmModal 은 bool 만 dismiss 하지만 escape 로 닫히면 None
            # 일 수도 있어 안전하게 truth check.
            if not yes:
                self.set_status("삭제 취소됨.")
                return
            self._submit_delete(target)

        self.app.push_screen(ConfirmModal(msg, title="거래 삭제 확인"), _on_close)

    # ---- mutation workers --------------------------------------------

    @work(exclusive=True, group="mutate", name="create_entry")
    async def _submit_create(self, draft: EntryDraft) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        # account_id 의 type 을 SessionState 에서 조회해 함께 보낸다.
        l_type = self._account_type(draft.l_account_id)
        r_type = self._account_type(draft.r_account_id)
        if not l_type or not r_type:
            self.set_status("계정 type 조회 실패 — accounts-list 를 다시 받으세요.", error=True)
            return
        try:
            await self._client.create_entry(
                section_id=session.section_id,
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
            self.set_status(f"거래 생성 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("create_entry failed")
            self.set_status(f"거래 생성 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status("거래 생성 완료. 재로드 중…")
        self.refresh_entries()

    @work(exclusive=True, group="mutate", name="update_entry")
    async def _submit_update(self, draft: EntryDraft) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not draft.entry_id:
            self.set_status("entry_id 가 없습니다 — 수정 불가.", error=True)
            return
        l_type = self._account_type(draft.l_account_id)
        r_type = self._account_type(draft.r_account_id)
        try:
            await self._client.update_entry(
                section_id=session.section_id,
                entry_id=draft.entry_id,
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
            self.set_status(f"거래 수정 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("update_entry failed")
            self.set_status(f"거래 수정 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status("거래 수정 완료. 재로드 중…")
        self.refresh_entries()

    @work(exclusive=True, group="mutate", name="delete_entry")
    async def _submit_delete(self, target: dict[str, Any]) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        eid = target.get("entry_id")
        if not eid:
            return
        try:
            await self._client.delete_entry(
                section_id=session.section_id, entry_id=str(eid),
            )
        except ToolError as e:
            self.set_status(f"거래 삭제 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("delete_entry failed")
            self.set_status(f"거래 삭제 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(f"거래 {eid} 삭제 완료. 재로드 중…")
        self.refresh_entries()

    def _account_type(self, account_id: str) -> str:
        """SessionState 의 flat 에서 account_id 의 type 키 (assets 등)."""
        session = self.app.session  # type: ignore[attr-defined]
        for a in session.accounts_flat:
            if a.get("account_id") == account_id:
                return a.get("type") or ""
        return ""

    # ---- worker --------------------------------------------------------

    @work(exclusive=True, group="entries", name="refresh_entries")
    async def refresh_entries(self) -> None:
        """sections / accounts / entries 를 chain 으로 부팅 / 재로드.

        section_id 가 비어있으면 sections-list → 자동 활성화.
        accounts_flat 이 비어있으면 accounts-list → SessionState 인덱스
        빌드. 마지막에 entries-list → DataTable 갱신.
        """
        session = self.app.session  # type: ignore[attr-defined]

        # 1. 섹션 미선택 시 sections-list + 자동 활성화 (HomeScreen 자리에서
        #    하던 일이 본 화면으로 흡수된 결과 — CL #51023).
        if not session.section_id:
            try:
                sections = await self._client.list_sections()
            except ToolError as e:
                self.set_status(f"섹션 로드 실패 [{e.kind}] {e.message}", error=True)
                return
            except Exception as e:  # pragma: no cover
                log.exception("entries bootstrap: list_sections failed")
                self.set_status(f"섹션 로드 실패 (INTERNAL): {e}", error=True)
                return
            if not sections:
                self.set_status(
                    "후잉 계정에 섹션이 없습니다 — whooing.com 에서 먼저 생성하세요.",
                    error=True,
                )
                return
            # 자동 활성화 우선순위 (CL #51031+):
            #   ① 저장된 last_section_id (사용자가 한 번이라도 's' 로 직접
            #      선택했으면 그 선택을 다음 부팅에 복원).
            #   ② "Default" 섹션 — title="Default" 또는 응답의 is_default=true.
            #      후잉이 새 계정에 default 로 만드는 섹션의 표준 이름.
            #   ③ WHOOING_SECTION_ID 환경변수 — legacy fallback.
            #   ④ 첫 섹션 — 그것마저 매칭 안 되면.
            chosen = None

            saved_sid = load_last_section_id()
            if saved_sid:
                chosen = next(
                    (s for s in sections
                     if str(s.get("section_id") or s.get("id")) == saved_sid),
                    None,
                )

            if chosen is None:
                chosen = next(
                    (s for s in sections
                     if s.get("is_default") is True or s.get("title") == "Default"),
                    None,
                )

            if chosen is None:
                env_id = default_section_id_from_env()
                if env_id:
                    chosen = next(
                        (s for s in sections
                         if str(s.get("section_id") or s.get("id")) == env_id),
                        None,
                    )

            if chosen is None:
                chosen = sections[0]

            sid = str(chosen.get("section_id") or chosen.get("id"))
            session.set_section(sid, chosen.get("title"))
            # 자동 활성화도 저장 — 다음 부팅에 같은 결정이 빠르게 적용된다.
            save_last_section_id(sid)

        # 2. accounts 캐시 미로드 시 fetch.
        if not session.accounts_flat:
            try:
                raw = await self._client.list_accounts(session.section_id)
            except ToolError as e:
                self.set_status(f"계정과목 로드 실패 [{e.kind}] {e.message}", error=True)
                return
            except Exception as e:  # pragma: no cover
                log.exception("entries bootstrap: list_accounts failed")
                self.set_status(f"계정과목 로드 실패 (INTERNAL): {e}", error=True)
                return
            flat = WhooingClient.flatten_accounts(raw)
            session.set_accounts(raw, flat)

        # 3. entries-list (기존 로직).
        section_id = session.section_id
        end_date = today_yyyymmdd()
        start_date = days_ago_yyyymmdd(self._window_days - 1)
        try:
            entries = await self._client.list_entries(section_id, start_date, end_date)
        except ToolError as e:
            self.set_status(f"거래내역 로드 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("entries refresh failed")
            self.set_status(f"거래내역 로드 실패 (INTERNAL): {e}", error=True)
            return

        # 후잉 응답은 보통 최근 → 과거 순. 사용자에게도 같은 순서로 보여준다.
        # entry_date desc, 같은 날짜는 entry_id desc (있다면) 로 보조 정렬.
        entries_sorted = sorted(
            entries,
            key=lambda e: (e.get("entry_date") or "", str(e.get("entry_id") or "")),
            reverse=True,
        )

        self._entries = entries_sorted
        self.last_entry_count = len(entries_sorted)
        self._render_table(entries_sorted)
        self._update_window_status(start_date, end_date, entries_sorted)

    # ---- render --------------------------------------------------------

    def _render_table(self, entries: list[dict[str, Any]]) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        table = self.query_one("#entries-table", DataTable)
        table.clear()
        for e in entries:
            date_s = e.get("entry_date") or ""
            money_s = _fmt_money(e.get("money"))
            l_id = e.get("l_account_id") or ""
            r_id = e.get("r_account_id") or ""
            l_name = session.title_of(l_id) if l_id else ""
            r_name = session.title_of(r_id) if r_id else ""
            item = e.get("item") or ""
            memo = e.get("memo") or ""
            table.add_row(date_s, money_s, l_name, r_name, item, memo)

    def _update_window_status(
        self,
        start_date: str,
        end_date: str,
        entries: list[dict[str, Any]],
    ) -> None:
        n = len(entries)
        # 100-cap 경고: 단일 일자에 100건이 모인 entries 가 있으면 누락 가능성.
        # entries 응답은 entry_date 별로 cluster 가능 — 같은 date 가 정확히
        # _SERVER_PAGE_CAP 개면 그 일자가 cap 도달 가능성을 의심한다.
        per_date: dict[str, int] = {}
        for e in entries:
            d = e.get("entry_date") or ""
            per_date[d] = per_date.get(d, 0) + 1
        cap_dates = [d for d, c in per_date.items() if c >= self._SERVER_PAGE_CAP]
        self.last_cap_warning = bool(cap_dates)

        section_id = self.app.session.section_id  # type: ignore[attr-defined]
        section_title = self.app.session.section_title  # type: ignore[attr-defined]
        sec_label = (
            f"{section_title} ({section_id})" if section_title else str(section_id)
        )

        if n == 0:
            # 빈 결과 — 사용자가 *왜* 비어있는지 한눈에 알 수 있도록 다음
            # 액션을 status bar 에 명시. warn 클래스로 시각 구분.
            msg = (
                f"거래내역 없음 — section={sec_label}, 최근 {self._window_days}일 "
                f"({start_date}~{end_date}). "
                f"다른 섹션 [s] / 윈도우 확장 [+] / 새 거래 [n]"
            )
            self.set_status(msg, warn=True)
            return

        msg = (
            f"{n}건 표시 ({start_date} ~ {end_date}, 최근 {self._window_days}일, "
            f"section={sec_label})"
        )
        if self.last_cap_warning:
            cap_list = ", ".join(cap_dates[:3]) + (" …" if len(cap_dates) > 3 else "")
            msg += f"  ⚠ 100-cap 도달 가능 ({cap_list})"
            self.set_status(msg, warn=True)
        else:
            self.set_status(msg)

    # ---- status bar ----------------------------------------------------

    def set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
