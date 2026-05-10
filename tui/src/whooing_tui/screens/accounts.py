"""AccountsScreen + AccountEditDialog — 계정과목 조회 / 추가 / 수정 / 삭제.

EntriesScreen 에서 `a` 키로 push. 활성 섹션의 계정과목을 type 별 (assets /
liabilities / capital / income / expenses / group) 트리로 표시. 사용자가
선택한 노드에서 `n` (new) / Enter (edit) / `d` (delete) 액션을 수행할 수
있다.

CRUD 는 `WhooingClient.create_account / update_account / delete_account /
check_account_deletable` 를 호출. 삭제는 항상 `check_deletable` 후 사용자
확인을 거친다 — 거래내역이 있는 항목은 후잉이 거부할 수 있어 close_date
변경 (비활성화) 을 권장하는 안내도 함께.

CRUD 후에는 SessionState.set_accounts 로 캐시 갱신 + Tree 재렌더.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, Footer, Header, Input, Label, Select, Static, Tree,
)

from whooing_tui.client import WhooingClient
from whooing_tui.dates import parse_yyyymmdd, today_yyyymmdd
from whooing_tui.ime import bind_ko
from whooing_tui.models import ToolError
from whooing_tui.screens.edit_entry import ConfirmModal
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


# 후잉 표준 type 표시 순서. group 은 마지막 (가상 계층).
_ACCOUNT_TYPES = ("assets", "liabilities", "capital", "income", "expenses")
_TYPE_LABEL = {
    "assets": "자산",
    "liabilities": "부채",
    "capital": "자본",
    "income": "수입",
    "expenses": "지출",
    "group": "그룹",
}

# 항목 종류 (category) — schema 기반.
_CATEGORIES = ("normal", "client", "creditcard", "checkcard", "steady", "floating")

_CLOSE_DATE_INDEFINITE = "29991231"


# ---- AccountDraft ------------------------------------------------------


@dataclass
class AccountDraft:
    """폼이 dismiss 결과로 반환하는 draft."""
    account: str        # assets / liabilities / capital / expenses / income
    type: str           # account / group
    title: str
    open_date: str
    close_date: str = _CLOSE_DATE_INDEFINITE
    category: str = ""
    memo: str = ""
    # 수정 모드면 account_id, 새 입력이면 None.
    account_id: str | None = None


# ---- AccountEditDialog -------------------------------------------------


class AccountEditDialog(ModalScreen[AccountDraft | None]):
    """계정과목 추가/수정 모달. dismiss(AccountDraft | None)."""

    DEFAULT_CSS = """
    AccountEditDialog {
        align: center middle;
    }
    #acc-frame {
        /* CL #51120+: 좁은 터미널 대응. */
        width: 95%;
        max-width: 70;
        min-width: 30;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #acc-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #acc-grid {
        grid-size: 2 7;
        grid-columns: 12 1fr;
        grid-rows: 3 3 3 3 3 3 3;
        height: auto;
        padding: 1 0;
    }
    #acc-grid Label {
        padding: 1 1 0 0;
        content-align: right middle;
    }
    #acc-buttons {
        height: 3;
        align: center middle;
        padding-top: 1;
    }
    #acc-buttons Button {
        margin: 0 1;
        min-width: 12;
    }
    #acc-error {
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
        *,
        existing: dict[str, Any] | None = None,
        existing_type_key: str | None = None,
    ) -> None:
        """`existing` 이 주어지면 수정 모드 (account_id 보존)."""
        super().__init__()
        self._existing = existing or {}
        self._existing_type_key = existing_type_key or ""
        self._is_edit = bool(self._existing.get("account_id"))

    def compose(self) -> ComposeResult:
        title = "계정과목 수정" if self._is_edit else "계정과목 추가"
        # account 종류: existing 의 type_key (assets/expenses/...) prefill.
        # update 시 사용자가 변경 가능 — 후잉이 허용.
        acc_options = [(_TYPE_LABEL[t], t) for t in _ACCOUNT_TYPES]
        acc_initial = self._existing_type_key if self._existing_type_key in _ACCOUNT_TYPES else _ACCOUNT_TYPES[0]

        type_options = [("일반 항목 (account)", "account"), ("표시용 그룹 (group)", "group")]
        type_initial = self._existing.get("type_kind") or "account"
        if type_initial not in ("account", "group"):
            type_initial = "account"

        cat_options = [("(자동)", "")] + [(c, c) for c in _CATEGORIES]
        cat_initial = self._existing.get("category") or ""
        if cat_initial and cat_initial not in _CATEGORIES:
            cat_initial = ""

        with Vertical(id="acc-frame"):
            yield Static(f"[bold]{title}[/bold]", id="acc-title")
            with Grid(id="acc-grid"):
                yield Label("title")
                yield Input(
                    value=self._existing.get("title") or "",
                    placeholder="항목 이름 (최대 30자)",
                    id="f-title", max_length=30,
                )
                yield Label("account")
                yield Select(acc_options, value=acc_initial, id="f-account", allow_blank=False)
                yield Label("type")
                yield Select(type_options, value=type_initial, id="f-type", allow_blank=False)
                yield Label("open_date")
                yield Input(
                    value=self._existing.get("open_date") or today_yyyymmdd(),
                    placeholder="YYYYMMDD",
                    id="f-open", max_length=8,
                )
                yield Label("close_date")
                yield Input(
                    value=self._existing.get("close_date") or _CLOSE_DATE_INDEFINITE,
                    placeholder="YYYYMMDD (29991231 = 무기한)",
                    id="f-close", max_length=8,
                )
                yield Label("category")
                yield Select(cat_options, value=cat_initial, id="f-category", allow_blank=False)
                yield Label("memo")
                yield Input(
                    value=self._existing.get("memo") or "",
                    placeholder="(선택, 최대 80자)",
                    id="f-memo", max_length=80,
                )
            yield Static("", id="acc-error")
            with Horizontal(id="acc-buttons"):
                yield Button("Save (Ctrl+S)", id="btn-save", variant="primary")
                yield Button("Cancel (Esc)", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#f-title", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        draft = self._build_draft()
        if isinstance(draft, AccountDraft):
            self.dismiss(draft)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.action_cancel()
        elif event.button.id == "btn-save":
            self.action_save()

    def _build_draft(self) -> AccountDraft | None:
        title = self.query_one("#f-title", Input).value.strip()
        if not title:
            self._show_error("title 은 필수입니다.")
            return None
        try:
            open_date = parse_yyyymmdd(self.query_one("#f-open", Input).value)
        except ValueError as e:
            self._show_error(f"open_date: {e}")
            return None
        try:
            close_date = parse_yyyymmdd(
                self.query_one("#f-close", Input).value or _CLOSE_DATE_INDEFINITE,
            )
        except ValueError as e:
            self._show_error(f"close_date: {e}")
            return None
        if close_date < open_date:
            self._show_error("close_date 가 open_date 보다 이전일 수 없습니다.")
            return None

        return AccountDraft(
            title=title,
            account=str(self.query_one("#f-account", Select).value),
            type=str(self.query_one("#f-type", Select).value),
            open_date=open_date,
            close_date=close_date,
            category=str(self.query_one("#f-category", Select).value or ""),
            memo=self.query_one("#f-memo", Input).value.strip(),
            account_id=self._existing.get("account_id"),
        )

    def _show_error(self, msg: str) -> None:
        self.query_one("#acc-error", Static).update(msg)


# ---- AccountsScreen ----------------------------------------------------


class AccountsScreen(Screen):
    """활성 섹션의 계정과목 트리 + CRUD."""

    DEFAULT_CSS = """
    AccountsScreen {
        layers: base;
    }
    #acc-body {
        height: 1fr;
        padding: 0 1;
    }
    #accounts-tree {
        height: 1fr;
        border: round $accent;
    }
    #acc-status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #acc-status.error {
        color: $error;
    }
    #acc-status.warn {
        color: $warning;
    }
    """

    BINDINGS = [
        *bind_ko("q", "back", "Back", show=True),
        Binding("escape", "back", "Back", show=False),
        *bind_ko("r", "refresh", "Refresh", show=True, priority=True),
        *bind_ko("n", "new_account", "New", show=True, priority=True),
        Binding("enter", "edit_account", "Edit", show=True, priority=True),
        *bind_ko("d", "delete_account", "Delete", show=True, priority=True),
        Binding("question_mark", "help", "Help", show=True, priority=True, key_display="?"),
    ]

    def __init__(self, client: WhooingClient) -> None:
        super().__init__()
        self._client = client
        self.last_status: str = ""
        # Tree node 의 data 로 사용할 dict — leaf (account) 또는 None (그룹).
        # type_key 도 함께 보관해 update_account 의 account 파라미터로 사용.
        # 형태: {"account_id": "x20", "type_key": "expenses",
        #        "raw": {...full account dict...}}

    # ---- compose ------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="acc-body"):
            yield Tree("(섹션 미선택)", id="accounts-tree")
        yield Static("", id="acc-status")
        yield Footer()

    def on_mount(self) -> None:
        self._render_tree()
        tree = self.query_one("#accounts-tree", Tree)
        tree.focus()
        self.set_status("Enter 수정 / n 새로 / d 삭제 / r 재로드 / q 뒤로")

    # ---- actions ------------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        invalidate = getattr(self._client, "invalidate_section", None)
        session = self.app.session  # type: ignore[attr-defined]
        if session.section_id and callable(invalidate):
            invalidate(session.section_id)
        self.set_status("재로드 중…")
        self._refresh_accounts()

    def action_help(self) -> None:
        from whooing_tui.screens.help import HelpModal
        self.app.push_screen(HelpModal("AccountsScreen", list(self.BINDINGS)))

    def action_new_account(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다.", error=True)
            return

        # 현재 cursor 가 가리키는 노드의 type 을 default 로 (그룹 노드면 그 type).
        type_key = self._cursor_type_key() or "expenses"

        def _on_close(draft: AccountDraft | None) -> None:
            if draft is None:
                self.set_status("입력 취소됨.")
                return
            self._submit_create(draft)

        self.app.push_screen(
            AccountEditDialog(existing_type_key=type_key),
            _on_close,
        )

    def action_edit_account(self) -> None:
        target = self._cursor_account()
        if target is None:
            self.set_status("선택된 항목이 없거나 그룹 노드입니다 — 일반 항목을 선택하세요.", error=True)
            return

        existing = self._existing_dict(target)

        def _on_close(draft: AccountDraft | None) -> None:
            if draft is None:
                self.set_status("수정 취소됨.")
                return
            self._submit_update(draft)

        self.app.push_screen(
            AccountEditDialog(existing=existing, existing_type_key=target["type_key"]),
            _on_close,
        )

    def action_delete_account(self) -> None:
        target = self._cursor_account()
        if target is None:
            self.set_status("선택된 항목이 없습니다.", error=True)
            return
        # 사전 검사 후 confirm.
        self._submit_check_then_delete(target)

    # ---- worker chain -------------------------------------------------

    @work(exclusive=True, group="accounts", name="refresh_accounts")
    async def _refresh_accounts(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not session.section_id:
            self.set_status("활성 섹션이 없습니다.", error=True)
            return
        try:
            raw = await self._client.list_accounts(session.section_id)
        except ToolError as e:
            self.set_status(f"계정과목 로드 실패 [{e.kind}] {e.message}", error=True)
            return
        flat = WhooingClient.flatten_accounts(raw)
        session.set_accounts(raw, flat)
        self._render_tree()
        self.set_status(
            f"섹션 {session.section_id}: 계정과목 {len(flat)}개 로드 완료.",
        )

    @work(exclusive=True, group="acc-mutate", name="create_account")
    async def _submit_create(self, draft: AccountDraft) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        try:
            await self._client.create_account(
                section_id=session.section_id,
                account=draft.account,
                type=draft.type,
                title=draft.title,
                open_date=draft.open_date,
                close_date=(draft.close_date if draft.close_date != _CLOSE_DATE_INDEFINITE else None),
                category=(draft.category or None),
                memo=(draft.memo or None),
            )
        except ToolError as e:
            self.set_status(f"계정과목 생성 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("create_account failed")
            self.set_status(f"계정과목 생성 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(f"계정과목 추가 완료 ({draft.title}). 재로드 중…")
        self._refresh_accounts()

    @work(exclusive=True, group="acc-mutate", name="update_account")
    async def _submit_update(self, draft: AccountDraft) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        if not draft.account_id:
            self.set_status("account_id 가 없습니다 — 수정 불가.", error=True)
            return
        try:
            await self._client.update_account(
                section_id=session.section_id,
                account_id=draft.account_id,
                account=draft.account,
                type=draft.type,
                title=draft.title,
                open_date=draft.open_date,
                close_date=draft.close_date,
                category=(draft.category or None),
                memo=(draft.memo or None),
            )
        except ToolError as e:
            self.set_status(f"계정과목 수정 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("update_account failed")
            self.set_status(f"계정과목 수정 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(f"계정과목 수정 완료 ({draft.title}). 재로드 중…")
        self._refresh_accounts()

    @work(exclusive=True, group="acc-mutate", name="check_then_delete")
    async def _submit_check_then_delete(self, target: dict[str, Any]) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        # 1. 사전 검사 (entries 건수 / 잔액 / 마지막 항목)
        try:
            check = await self._client.check_account_deletable(
                section_id=session.section_id,
                account=target["type_key"],
                account_id=target["account_id"],
            )
        except ToolError as e:
            self.set_status(f"삭제 사전검사 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("check_account_deletable failed")
            self.set_status(f"삭제 사전검사 실패 (INTERNAL): {e}", error=True)
            return

        entries_count = check.get("entries_count")
        balance = check.get("balance")
        is_last = check.get("is_last")

        warn_lines = []
        if entries_count:
            warn_lines.append(f"  ⚠ 거래 내역 {entries_count}건 — 삭제 시 함께 사라집니다.")
        if balance:
            warn_lines.append(f"  ⚠ 잔액 {balance:,}")
        if is_last:
            warn_lines.append("  ⚠ 같은 type 의 마지막 항목 — 삭제 후 type 자체가 비게 됩니다.")
        if not warn_lines:
            warn_lines.append("  거래 내역 / 잔액 없음 — 안전한 삭제.")

        title = target.get("title") or target["account_id"]
        msg = (
            f"이 항목을 영구 삭제할까요?\n\n"
            f"  title       : {title}\n"
            f"  account_id  : {target['account_id']}\n"
            f"  type        : {target['type_key']}\n"
            f"\n사전검사 결과:\n" + "\n".join(warn_lines) +
            f"\n\n거래내역이 있으면 close_date 변경 (비활성화) 권장."
        )

        def _on_close(yes: bool | None) -> None:
            if not yes:
                self.set_status("삭제 취소됨.")
                return
            self._submit_delete(target)

        self.app.push_screen(
            ConfirmModal(msg, title="계정과목 삭제 확인"),
            _on_close,
        )

    @work(exclusive=True, group="acc-mutate", name="delete_account")
    async def _submit_delete(self, target: dict[str, Any]) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        try:
            await self._client.delete_account(
                section_id=session.section_id,
                account=target["type_key"],
                account_id=target["account_id"],
            )
        except ToolError as e:
            self.set_status(f"계정과목 삭제 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("delete_account failed")
            self.set_status(f"계정과목 삭제 실패 (INTERNAL): {e}", error=True)
            return
        self.set_status(
            f"계정과목 {target['account_id']} 삭제 완료. 재로드 중…",
        )
        self._refresh_accounts()

    # ---- 트리 렌더링 + cursor 헬퍼 ------------------------------------

    def _render_tree(self) -> None:
        session = self.app.session  # type: ignore[attr-defined]
        tree = self.query_one("#accounts-tree", Tree)
        title = session.section_title or session.section_id or "(섹션 미선택)"
        tree.reset(f"{title}  [dim]{session.section_id or ''}[/dim]")
        tree.root.expand()

        raw = session.accounts_raw or {}
        seen_types = list(raw.keys())
        ordered = [t for t in _ACCOUNT_TYPES if t in seen_types]
        ordered += sorted(t for t in seen_types if t not in _ACCOUNT_TYPES and t != "group")
        if "group" in seen_types:
            ordered.append("group")

        for t in ordered:
            items = raw.get(t)
            if not isinstance(items, list) or not items:
                continue
            label = _TYPE_LABEL.get(t, t)
            type_node = tree.root.add(
                f"[bold]{label}[/bold]  [dim]({len(items)})[/dim]",
                expand=True,
                data={"is_type": True, "type_key": t},
            )
            for a in items:
                aid = str(a.get("account_id") or a.get("id") or "")
                name = a.get("title") or a.get("name") or "(no title)"
                if not aid:
                    continue
                type_node.add_leaf(
                    f"{name}  [dim]{aid}[/dim]",
                    data={
                        "account_id": aid,
                        "title": name,
                        "type_key": t,
                        "raw": a,
                    },
                )

    def _cursor_account(self) -> dict[str, Any] | None:
        """현재 cursor 가 가리키는 leaf account dict (또는 None — 그룹/루트)."""
        tree = self.query_one("#accounts-tree", Tree)
        node = tree.cursor_node
        if node is None:
            return None
        data = node.data
        if not isinstance(data, dict):
            return None
        if data.get("is_type") or "account_id" not in data:
            return None
        return data

    def _cursor_type_key(self) -> str | None:
        """현재 cursor 가 가리키는 노드의 type_key (account 또는 group 상관없이)."""
        tree = self.query_one("#accounts-tree", Tree)
        node = tree.cursor_node
        if node is None or not isinstance(node.data, dict):
            return None
        return node.data.get("type_key")

    def _existing_dict(self, target: dict[str, Any]) -> dict[str, Any]:
        """AccountEditDialog 의 `existing` 으로 넘길 prefill dict.

        후잉 응답의 raw account 가 무엇을 노출하는지 정확히 알 수 없으나
        title / open_date / close_date / category / memo / type 같은 필드
        는 일반적이라 가정. type 은 'account' / 'group'.
        """
        raw = target.get("raw") or {}
        return {
            "account_id": target["account_id"],
            "title": target.get("title") or raw.get("title") or "",
            "open_date": raw.get("open_date") or "",
            "close_date": raw.get("close_date") or _CLOSE_DATE_INDEFINITE,
            "category": raw.get("category") or "",
            "memo": raw.get("memo") or "",
            "type_kind": raw.get("type") or "account",
        }

    # ---- status -------------------------------------------------------

    def set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one("#acc-status", Static)
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
