"""EntryEditDialog — 거래 추가/수정 모달.

ModalScreen[EntryDraft | None] 으로 열린다. 사용자가 저장하면 EntryDraft
객체를 dismiss 결과로 반환하고, 취소 / esc 면 None.

호출자(EntriesScreen)는 dismiss 결과를 받아 WhooingClient.create_entry /
update_entry 를 부른다 + 로컬 sqlite 의 annotations / hashtags 도 동기화.

UI 필드 (CL #51076+):
  date     YYYY-MM-DD (auto-dash — 숫자만 입력하면 자동으로 - 삽입,
           - 직접 타이핑은 무시).
  money    숫자, 입력 시 천단위 콤마 자동 포매팅 (1,234,567).
  left     계정과목 — Button 형태로 *이름* 표시. Enter / click 시
           AccountPickerScreen 으로 메뉴 선택.
  right    같은 규칙.
  item     적요 (Input).
  memo     메모 (후잉 + 로컬 db 양쪽에 저장).
  tags     해시태그 (로컬 db only). 공백/콤마/`#` 구분 — `식비, 저녁`
           또는 `#식비 #저녁`.
  attach   첨부파일 (수정 모드에서만, entry_id 가 있을 때) — 📎N 으로
           현재 개수 표시 + Enter / 클릭 시 AttachmentBrowserScreen
           push (a/d/o/e 로 추가/삭제/열기/note 편집). 신규 모드는
           entry_id 가 없어 disabled — 저장 후 다시 열면 사용 가능.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from whooing_tui.dates import parse_yyyymmdd, today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.state import SessionState


@dataclass
class EntryDraft:
    """사용자가 dialog 에서 확정한 거래 입력값.

    EntriesScreen 이 이 객체를 받아 client.create_entry / update_entry 의
    인자로 풀어 넣는다. `l_type` / `r_type` 은 picker 가 직접 채워 넣어
    SessionState 재조회 없이 바로 후잉 호출에 쓸 수 있다.
    """
    entry_date: str
    money: int
    l_account_id: str
    r_account_id: str
    l_type: str = ""           # CL #51076+: picker 결과 직접 보존
    r_type: str = ""           # CL #51076+
    item: str = ""
    memo: str = ""
    # CL #51076+: 로컬 sqlite 의 annotations/hashtags 동기화 — 후잉에는
    # 보내지 않는다. tags 는 normalize 된 list (중복/공백/`#` 처리).
    tags: list[str] = field(default_factory=list)
    # 수정 모드면 entry_id, 새 입력이면 None.
    entry_id: str | None = None


# ---- 입력 normalize helpers --------------------------------------------


def _digits_only(s: str, max_len: int | None = None) -> str:
    """숫자가 아닌 문자 (영문 / `-` / `,` / 공백 등) 모두 제거. 선택적 max_len."""
    out = re.sub(r"[^0-9]", "", s or "")
    if max_len is not None:
        out = out[:max_len]
    return out


def _format_date_dashed(digits: str) -> str:
    """`"20260509"` → `"2026-05-09"` 같은 부분 입력의 점진 포매팅.

    digits 길이에 따라:
      0~4   : 그대로 (예: "20" → "20")
      5~6   : "YYYY-M" / "YYYY-MM"
      7~8   : "YYYY-MM-D" / "YYYY-MM-DD"
    """
    if len(digits) >= 7:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(digits) >= 5:
        return f"{digits[:4]}-{digits[4:]}"
    return digits


def _format_money_comma(digits: str) -> str:
    """숫자 문자열을 천단위 콤마로. 빈 문자열이면 그대로."""
    if not digits:
        return ""
    return f"{int(digits):,}"


def _strip_comma_int(s: str) -> int:
    """천단위 콤마/공백 입력을 정수로. 빈 문자열이면 ValueError."""
    cleaned = (s or "").replace(",", "").strip()
    if not cleaned:
        raise ValueError("금액이 비어있습니다.")
    return int(cleaned)


def _parse_dashed_date_to_yyyymmdd(s: str) -> str:
    """`"2026-05-09"` (또는 `"20260509"`) 입력을 8자리 YYYYMMDD 로 정규화 후
    `parse_yyyymmdd` 검증 통과시킨다.
    """
    digits = _digits_only(s)
    return parse_yyyymmdd(digits)


def parse_hashtags_input(text: str) -> list[str]:
    """`"#식비 #외식, 점심"` → `["식비", "외식", "점심"]` (정규화 + dedup).

    공백 / 콤마 / `#` 모두 구분자. 양 끝 공백 제거. 중복 제거 (insertion order
    보존). 빈 토큰 무시.
    """
    if not text:
        return []
    tokens = re.split(r"[\s,#]+", text)
    seen: list[str] = []
    for t in tokens:
        t = t.strip()
        if t and t not in seen:
            seen.append(t)
    return seen


# ---- date / money input 위젯 -------------------------------------------


class _DateInput(Input):
    """숫자만 받아 `YYYY-MM-DD` 로 auto-format. `-` 직접 타이핑은 무시.

    `Input.Changed` 이벤트에서 raw value 의 숫자만 추출해 dash 자리에
    삽입한 string 으로 다시 set. 무한 루프 방지를 위해 `prevent` 사용.
    """

    def __init__(self, value: str = "", **kwargs: Any) -> None:
        # 초기 value 도 정규화 — `"20260509"` 로 들어와도 `"2026-05-09"` 표시.
        digits = _digits_only(value, max_len=8)
        super().__init__(value=_format_date_dashed(digits), **kwargs)

    @on(Input.Changed)
    def _on_changed(self, event: Input.Changed) -> None:
        if event.input is not self:
            return
        digits = _digits_only(self.value, max_len=8)
        formatted = _format_date_dashed(digits)
        if formatted != self.value:
            with self.prevent(Input.Changed):
                self.value = formatted
                # cursor 를 끝에 두는 게 사용자 흐름에 자연 (입력 직후).
                try:
                    self.cursor_position = len(formatted)
                except Exception:  # pragma: no cover
                    pass


class _MoneyInput(Input):
    """숫자만 받아 천단위 콤마 자동 포매팅.

    CL #51087+: 입력값을 오른쪽 정렬 (회계 컨벤션 + EntriesScreen 의 money
    컬럼과 시각 일치).
    """

    DEFAULT_CSS = """
    _MoneyInput {
        text-align: right;
    }
    """

    def __init__(self, value: str = "", **kwargs: Any) -> None:
        digits = _digits_only(value)
        super().__init__(value=_format_money_comma(digits), **kwargs)

    @on(Input.Changed)
    def _on_changed(self, event: Input.Changed) -> None:
        if event.input is not self:
            return
        digits = _digits_only(self.value)
        formatted = _format_money_comma(digits)
        if formatted != self.value:
            with self.prevent(Input.Changed):
                self.value = formatted
                try:
                    self.cursor_position = len(formatted)
                except Exception:  # pragma: no cover
                    pass


# ---- account button (left/right 자리에 표시되는 버튼) -------------------


class _AccountButton(Button):
    """left/right 자리에 들어가는 picker 버튼.

    label 은 "title (account_id)" 형태로 *이름* 노출. button 의 .data 를
    통해 EntryEditDialog 가 account_id / type 을 추적.
    """

    DEFAULT_CSS = """
    _AccountButton {
        width: 1fr;
        text-align: left;
    }
    """

    def __init__(
        self,
        *,
        account_id: str = "",
        title: str = "",
        type_key: str = "",
        button_id: str | None = None,
    ) -> None:
        super().__init__(
            self._make_label(title, account_id),
            id=button_id,
        )
        self.account_id = account_id
        # textual.widgets.Button 의 instance attribute 와 충돌 안 하도록
        # 다른 이름.
        self.acc_title = title
        self.type_key = type_key

    @staticmethod
    def _make_label(title: str, account_id: str) -> str:
        if not account_id:
            return "(엔터로 선택)"
        return f"{title or '?'}  ({account_id})"

    def set_account(self, account_id: str, title: str, type_key: str) -> None:
        self.account_id = account_id
        self.acc_title = title
        self.type_key = type_key
        self.label = self._make_label(title, account_id)


class _AttachmentButton(Button):
    """attach 자리에 들어가는 picker 버튼.

    label 은 첨부 개수에 따라 동적:
      - 수정 모드 + 0 개  : "📎 첨부 없음 — Enter 로 추가"
      - 수정 모드 + N개  : "📎 N개 첨부 — Enter 로 관리"
      - 신규 모드        : "📎 저장 후 첨부 가능" (disabled)

    `entry_id` 가 비어있으면 자동으로 disabled. AttachmentBrowserScreen 이
    entry_id 를 요구하므로 신규 모드는 의미상 무효.
    """

    DEFAULT_CSS = """
    _AttachmentButton {
        width: 1fr;
        text-align: left;
    }
    """

    def __init__(
        self,
        *,
        entry_id: str = "",
        count: int = 0,
        button_id: str | None = None,
    ) -> None:
        super().__init__(
            self._make_label(entry_id, count),
            id=button_id,
            disabled=not entry_id,
        )
        self.entry_id = entry_id
        self.count = count

    @staticmethod
    def _make_label(entry_id: str, count: int) -> str:
        if not entry_id:
            return "📎 저장 후 첨부 가능"
        if count == 0:
            return "📎 첨부 없음 — Enter 로 추가"
        return f"📎 {count}개 첨부 — Enter 로 관리"

    def set_count(self, count: int) -> None:
        self.count = count
        self.label = self._make_label(self.entry_id, count)


class EntryEditDialog(ModalScreen[EntryDraft | None]):
    """거래 추가/수정 모달. dismiss(EntryDraft | None)."""

    DEFAULT_CSS = """
    EntryEditDialog {
        align: center middle;
    }
    #dialog-frame {
        /* CL #51120+: 좁은 터미널 (iPhone Blink 등) 대응 — 95% 기본,
           max-width 76 으로 cap, min-width 30 으로 floor. */
        width: 95%;
        max-width: 76;
        min-width: 30;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #dialog-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #form-grid {
        grid-size: 2 8;
        grid-columns: 9 1fr;
        grid-rows: 3 3 3 3 3 3 3 3;
        height: auto;
        padding: 1 0;
    }
    #form-grid Label {
        padding: 1 1 0 0;
        content-align: right middle;
    }
    #button-row {
        height: 3;
        align: center middle;
        padding-top: 1;
    }
    #button-row Button {
        margin: 0 1;
        min-width: 18;
    }
    #form-error {
        height: auto;
        color: $error;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True, priority=True),
    ]

    def __init__(
        self,
        session: SessionState,
        *,
        existing: dict[str, Any] | None = None,
    ) -> None:
        """`existing` 이 주어지면 수정 모드 (값 prefill + entry_id 보존).

        `existing` 에는 후잉 거래 dict 외에 두 개의 보조 키를 끼워 넣을 수
        있다 (호출자가 sqlite 에서 미리 fetch — `EntriesScreen` 참조):
          - `_local_tags`: 이 entry 의 기존 해시태그 list (prefill 용).
          - `_all_tags_db`: 섹션 전체에서 본 적 있는 해시태그 사전
            `{tag: count}` (TagsPickerScreen 의 추천 / 자주 쓰는 태그 출처).
        """
        super().__init__()
        self._session = session
        self._existing = existing or {}
        self._is_edit = bool(self._existing.get("entry_id"))
        # left/right 의 초기 (account_id, title, type) — _AccountButton 에 set.
        self._initial_left = self._lookup_account(self._existing.get("l_account_id"))
        self._initial_right = self._lookup_account(self._existing.get("r_account_id"))
        # tags picker 가 사용하는 사전. dict 가 비어있어도 모달은 정상 동작
        # (추천 / 자주 쓰는 태그 섹션이 비어있을 뿐).
        raw_db = self._existing.get("_all_tags_db") or {}
        self._all_tags_db: dict[str, int] = (
            dict(raw_db) if isinstance(raw_db, dict) else {}
        )

    def _lookup_account(self, aid: str | None) -> tuple[str, str, str]:
        """account_id → (id, title, type). 못 찾으면 (id, "", "")."""
        if not aid:
            return ("", "", "")
        for a in self._session.accounts_flat:
            if a.get("account_id") == aid:
                return (aid, a.get("title") or "", a.get("type") or "")
        return (aid, "", "")

    def compose(self) -> ComposeResult:
        title = "거래 수정" if self._is_edit else "거래 추가"
        # 초기 date 값: existing 이 8자리 YYYYMMDD 면 그대로, 신규면 today.
        date_init = self._existing.get("entry_date") or today_yyyymmdd()
        money_init = str(self._existing.get("money") or "")
        # CL #51115+: tags 는 사용자 입장에서 항상 `#` 시작으로 보이도록.
        # 내부 저장은 bare (#X 없이) — `parse_hashtags_input` 가 분리/스트립.
        tags_init = " ".join(
            f"#{t}" for t in (self._existing.get("_local_tags") or [])
        )
        with Vertical(id="dialog-frame"):
            yield Static(f"[bold]{title}[/bold]", id="dialog-title")
            with Grid(id="form-grid"):
                yield Label("date")
                yield _DateInput(
                    value=date_init,
                    placeholder="YYYY-MM-DD", id="f-date",
                )
                yield Label("money")
                yield _MoneyInput(
                    value=money_init,
                    placeholder="숫자 (자동 콤마 포매팅)", id="f-money",
                )
                yield Label("left")
                yield _AccountButton(
                    account_id=self._initial_left[0],
                    title=self._initial_left[1],
                    type_key=self._initial_left[2],
                    button_id="f-left",
                )
                yield Label("right")
                yield _AccountButton(
                    account_id=self._initial_right[0],
                    title=self._initial_right[1],
                    type_key=self._initial_right[2],
                    button_id="f-right",
                )
                yield Label("item")
                yield Input(
                    value=self._existing.get("item") or "",
                    placeholder="적요 (예: 스타벅스)", id="f-item",
                )
                yield Label("memo")
                yield Input(
                    value=self._existing.get("memo") or "",
                    placeholder="(후잉 + 로컬 db 양쪽 저장)", id="f-memo",
                )
                yield Label("tags")
                yield Input(
                    value=tags_init,
                    placeholder="해시태그 (로컬 db only). 예: #식비 #저녁",
                    id="f-tags",
                )
                yield Label("attach")
                entry_id_str = str(self._existing.get("entry_id") or "")
                yield _AttachmentButton(
                    entry_id=entry_id_str,
                    count=self._fetch_attachment_count() if entry_id_str else 0,
                    button_id="f-attachments",
                )
            # CL #51149+ (H7): typing 중 매칭 태그 hint Static — Grid 밖으로
            # (CL #52731+, 아래 attach row 가 grid 안에 들어가도록).
            # Grid 의 grid-size:2 8 안에 들어가면 col 자리를 가로채 attach 가
            # 밀려난다. 시각적으론 form 바로 아래 한 줄 — typing 시점에만
            # 의미가 있는 hint 라 위치 영향 미미.
            yield Static("", id="f-tags-hint")
            yield Static("", id="form-error")
            with Horizontal(id="button-row"):
                yield Button("Save (Ctrl+S)", id="btn-save", variant="primary")
                yield Button("Cancel (Esc)", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#f-date", Input).focus()

    # ---- actions ------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        draft = self._build_draft()
        if isinstance(draft, EntryDraft):
            self.dismiss(draft)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-cancel":
            self.action_cancel()
        elif bid == "btn-save":
            self.action_save()
        elif bid in ("f-left", "f-right"):
            # left/right 버튼 — 계정과목 picker 모달.
            self._open_account_picker(bid)
        elif bid == "f-attachments":
            self._open_attachments()

    def on_screen_resume(self) -> None:
        """attachment browser 가 닫히고 본 dialog 가 다시 보이게 되면
        attach 버튼의 count 를 재계산 — browser 안에서 a/d 했을 수 있다.
        신규 모드 (button disabled) 면 아무 것도 안 함.
        """
        try:
            btn = self.query_one("#f-attachments", _AttachmentButton)
        except Exception:
            return
        if not btn.entry_id:
            return
        btn.set_count(self._fetch_attachment_count())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """tags Input 위에서 Enter — TagsPickerScreen push.

        Enter 가 자연스러운 트리거지만 다른 Input (date / money / item /
        memo) 에서는 default 동작 (focus 이동) 그대로 둬야 하므로 id 분기.
        """
        if event.input.id != "f-tags":
            return
        self._open_tags_picker()

    def on_input_changed(self, event: Input.Changed) -> None:
        """CL #51149+ (H7): tags Input typing 중 hint 갱신 — top 3 매칭."""
        if event.input.id != "f-tags":
            return
        self._refresh_tags_hint()

    def _refresh_tags_hint(self) -> None:
        """현재 tags Input value 의 마지막 토큰을 prefix 로 매칭. 비면 빈 hint."""
        try:
            tags_input = self.query_one("#f-tags", Input)
            hint_static = self.query_one("#f-tags-hint", Static)
        except Exception:  # pragma: no cover
            return
        from whooing_tui.screens.tags_picker import filter_tags

        raw = tags_input.value
        # 마지막 토큰만 — 사용자가 지금 타이핑 중인 것.
        # whitespace/콤마/`#` 으로 split 한 마지막 비빈 token.
        import re as _re
        tokens = _re.split(r"[\s,]+", raw)
        last = tokens[-1].lstrip("#").strip() if tokens else ""
        if not last:
            hint_static.update("")
            return
        # CL #52757+: 이미 입력란에 있는 태그 — **마지막 token 도 포함** —
        # 추천에서 모두 제외. 종전엔 `tokens[:-1]` 만 제외라서 사용자가
        # 이미 "#보안" 까지 다 친 경우에도 hint 에 "#보안" 추천 (사용자
        # 보고: "이미 보안 태그가 붙어있는데 보안 태그를 추천해줍니다").
        # last 의 prefix 매칭 후보 중 *정확히 last 와 같은* 태그만 제외.
        existing_in_input = set()
        for t in tokens:
            t = t.lstrip("#").strip()
            if t:
                existing_in_input.add(t)
        candidates = [
            t for t in self._all_tags_db.keys()
            if t not in existing_in_input
        ]
        matched = filter_tags(last, candidates)[:3]
        if not matched:
            hint_static.update("[dim](매칭 없음)[/dim]")
            return
        # `#tag (count)` 3개 까지.
        bits = []
        for t in matched:
            count = self._all_tags_db.get(t, 0)
            bits.append(f"#{t}[dim]({count})[/dim]")
        hint_static.update("[dim]💡 추천:[/dim] " + " ".join(bits))

    def _open_account_picker(self, button_id: str) -> None:
        """left/right 버튼 → AccountPickerScreen push, 결과로 버튼 갱신."""
        from whooing_tui.screens.account_picker import AccountPickerScreen

        side = "left" if button_id == "f-left" else "right"
        btn = self.query_one(f"#{button_id}", _AccountButton)

        def _on_pick(result: tuple[str, str, str] | None) -> None:
            if result is None:
                return
            aid, title, type_key = result
            btn.set_account(aid, title, type_key)

        self.app.push_screen(
            AccountPickerScreen(
                self._session, side=side, current_id=btn.account_id or None,
            ),
            _on_pick,
        )

    def _fetch_attachment_count(self) -> int:
        """현재 수정 중인 entry 의 첨부 개수. 실패 / 신규 모드면 0.

        entries.py 의 `_fetch_all_attachment_counts` 와 같은 패턴 — 단
        일 entry 만 query. db 없음 / 권한 / 섹션 unset 등은 모두 0 으로
        graceful degrade (dialog 자체는 정상 열림).
        """
        eid = self._existing.get("entry_id")
        if not eid:
            return 0
        try:
            from whooing_core import attachments as core_attach
            from whooing_tui import data as tui_data
        except Exception:  # pragma: no cover
            return 0
        sid = self._session.section_id or None
        try:
            with tui_data.open_ro() as conn:
                m = core_attach.list_attachments_for(
                    conn, [str(eid)], section_id=sid,
                )
        except Exception:
            return 0
        return len(m.get(str(eid), []))

    def _open_attachments(self) -> None:
        """attach 버튼 → AttachmentBrowserScreen push.

        AttachmentBrowserScreen 은 a/d/o/e/r 로 첨부를 추가/삭제/열기/note
        편집/새로고침. 우리 dialog 는 그동안 push stack 아래에서 살아있다가
        `on_screen_resume` 에서 count 를 다시 fetch.
        """
        eid = self._existing.get("entry_id")
        if not eid:
            return
        from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen

        self.app.push_screen(
            AttachmentBrowserScreen(
                entry_id=str(eid),
                section_id=self._session.section_id or None,
            ),
        )

    def _open_tags_picker(self) -> None:
        """tags Input Enter → TagsPickerScreen push, 결과 → 입력란 append."""
        from whooing_tui.screens.tags_picker import TagsPickerScreen

        item_now = self.query_one("#f-item", Input).value
        memo_now = self.query_one("#f-memo", Input).value
        tags_input = self.query_one("#f-tags", Input)
        already = parse_hashtags_input(tags_input.value)

        def _on_pick(result: str | None) -> None:
            if not result:
                return
            tag = result.strip().lstrip("#").strip()
            if not tag:
                return
            # 이미 있는 토큰이면 noop (중복 입력 방지).
            if tag in already:
                tags_input.focus()
                return
            # CL #51115+: 사용자 시각으로 `#` 시작이 자연스러우므로 항상
            # `#` prefix 로 append. parse_hashtags_input 가 저장 시 strip.
            current = tags_input.value.rstrip()
            display = f"#{tag}"
            tags_input.value = (
                f"{current} {display}" if current else display
            )
            tags_input.cursor_position = len(tags_input.value)
            tags_input.focus()

        self.app.push_screen(
            TagsPickerScreen(
                item=item_now,
                memo=memo_now,
                existing=self._all_tags_db,
                already_selected=already,
            ),
            _on_pick,
        )

    # ---- form → draft -------------------------------------------------

    def _build_draft(self) -> EntryDraft | None:
        """폼 값을 EntryDraft 로. 검증 실패 시 form-error 에 메시지를 쓰고
        None 을 반환 (dialog 는 닫지 않는다)."""
        date_raw = self.query_one("#f-date", Input).value
        money_raw = self.query_one("#f-money", Input).value
        item_raw = self.query_one("#f-item", Input).value
        memo_raw = self.query_one("#f-memo", Input).value
        tags_raw = self.query_one("#f-tags", Input).value
        left_btn = self.query_one("#f-left", _AccountButton)
        right_btn = self.query_one("#f-right", _AccountButton)

        try:
            date = _parse_dashed_date_to_yyyymmdd(date_raw)
        except ValueError as e:
            self._show_error(f"date: {e}")
            return None
        try:
            money = _strip_comma_int(money_raw)
        except ValueError as e:
            self._show_error(f"money: {e}")
            return None
        if money <= 0:
            self._show_error("money 는 양수여야 합니다 (음양은 차변/대변으로 표현).")
            return None
        if not left_btn.account_id:
            self._show_error("left: 엔터로 계정과목을 선택하세요.")
            return None
        if not right_btn.account_id:
            self._show_error("right: 엔터로 계정과목을 선택하세요.")
            return None
        if left_btn.account_id == right_btn.account_id:
            self._show_error("left 와 right 는 서로 다른 항목이어야 합니다.")
            return None

        return EntryDraft(
            entry_date=date,
            money=money,
            l_account_id=left_btn.account_id,
            r_account_id=right_btn.account_id,
            l_type=left_btn.type_key,
            r_type=right_btn.type_key,
            item=item_raw.strip(),
            memo=memo_raw.strip(),
            tags=parse_hashtags_input(tags_raw),
            entry_id=self._existing.get("entry_id"),
        )

    def _show_error(self, msg: str) -> None:
        self.query_one("#form-error", Static).update(msg)


# CL #52834+: 이전엔 본 파일에 자체 ConfirmModal 정의가 있었으나 CL #51156
# 의 `widgets/confirm.ConfirmModal` 과 책임 중복이었다. 본 파일은 후방 호환
# alias 만 유지 — 기존 import (`from whooing_tui.screens.edit_entry import
# ConfirmModal`) 가 깨지지 않도록.
from whooing_tui.widgets.confirm import ConfirmModal  # noqa: F401, E402
