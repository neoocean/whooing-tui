"""HomeScreen — 첫 진입 화면.

왼쪽에 섹션 picker, 오른쪽에 활성 섹션의 계정과목 트리. 시작 시
sections-list 를 비동기로 가져와 picker 에 채우고, 사용자가 섹션을
선택하면 accounts-list 를 호출해 트리를 갱신한다.

결정 사항:
  - 섹션 picker 는 OptionList — 단순 키보드 navigation 과 highlight 메시지
    가 충분하고, DataTable 보다 가볍다.
  - 계정과목 트리는 Tree — type (assets/liabilities/...) 그룹 + 자식
    node 로 account_id 를 단다.
  - 후잉 호출은 `@work(exclusive=True)` 로 같은 종류 호출을 직렬화. 사용자
    가 섹션을 빠르게 여러 번 바꿔도 마지막 1개만 결과를 적용한다.
  - 인증/네트워크 에러는 화면 하단 status bar 에 표시 — 모달은 띄우지
    않는다 (Phase 2a 는 화면 1개로 단순 유지).
"""

from __future__ import annotations

import logging
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static, Tree
from textual.widgets.option_list import Option

from whooing_tui.client import WhooingClient
from whooing_tui.models import ToolError

log = logging.getLogger(__name__)


# Tree node 의 type 표시 순서 — 후잉 표준 (assets → liabilities → capital
# → income → expenses) 을 따른다. group 은 가상 계층이라 마지막에 오면
# 다른 type 들 사이에 흩어지지 않는다.
_TYPE_ORDER = ("assets", "liabilities", "capital", "income", "expenses", "group")

_TYPE_LABEL = {
    "assets": "자산",
    "liabilities": "부채",
    "capital": "자본",
    "income": "수입",
    "expenses": "지출",
    "group": "그룹",
}


class HomeScreen(Screen):
    """섹션 picker (좌) + 계정과목 트리 (우)."""

    DEFAULT_CSS = """
    HomeScreen {
        layers: base;
    }
    #home-body {
        height: 1fr;
    }
    #sections-pane {
        width: 32;
        border: round $accent;
        padding: 0 1;
    }
    #accounts-pane {
        width: 1fr;
        border: round $accent;
        padding: 0 1;
    }
    #status {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    #status.error {
        color: $error;
    }
    OptionList {
        height: 1fr;
    }
    Tree {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh", show=True),
        Binding("ctrl+l", "refresh", "Refresh", show=False),
        Binding("escape", "focus_sections", "Focus sections", show=False),
    ]

    def __init__(self, client: WhooingClient) -> None:
        super().__init__()
        self._client = client
        # raw sections cache 는 highlight → set_section 시 title 을 즉시
        # 알려주기 위해 보관. SessionState 가 메인 store.
        self._sections_by_id: dict[str, dict[str, Any]] = {}
        # 마지막 status bar 텍스트 — 테스트가 Static.render 의 사적 API 에
        # 의존하지 않고 평문으로 확인할 수 있게 한다.
        self.last_status: str = ""

    # ---- compose -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="home-body"):
            with Vertical(id="sections-pane"):
                yield Static("[bold]섹션 (Sections)[/bold]", id="sections-title")
                yield OptionList(id="sections-list")
            with Vertical(id="accounts-pane"):
                yield Static(
                    "[bold]계정과목 (Accounts)[/bold] — 섹션을 선택하세요.",
                    id="accounts-title",
                )
                yield Tree("(섹션 미선택)", id="accounts-tree")
        yield Static("", id="status")
        yield Footer()

    # ---- mount ---------------------------------------------------------

    def on_mount(self) -> None:
        # 첫 진입 시 sections-list 즉시 호출. 사용자 액션 없이 채워준다.
        self.set_status("후잉 섹션 목록을 불러오는 중…")
        self.refresh_sections()
        self.query_one("#sections-list", OptionList).focus()

    # ---- 액션 ----------------------------------------------------------

    def action_refresh(self) -> None:
        """현재 활성 섹션이 있으면 accounts 만, 없으면 sections 부터 재로드."""
        if self.app.session.section_id:  # type: ignore[attr-defined]
            self.set_status("계정과목을 다시 불러오는 중…")
            self.refresh_accounts(self.app.session.section_id)  # type: ignore[attr-defined]
        else:
            self.set_status("섹션 목록을 다시 불러오는 중…")
            self.refresh_sections()

    def action_focus_sections(self) -> None:
        try:
            self.query_one("#sections-list", OptionList).focus()
        except Exception:
            pass

    # ---- sections worker ----------------------------------------------

    @work(exclusive=True, group="sections", name="refresh_sections")
    async def refresh_sections(self) -> None:
        """sections-list 비동기 호출 + picker 갱신.

        결과의 첫 항목을 자동 선택해서 accounts 까지 chain — 사용자가 한
        번도 안 눌러도 화면이 의미있게 채워지도록.
        """
        try:
            sections = await self._client.list_sections()
        except ToolError as e:
            self.set_status(f"섹션 로드 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover — 예상치 못한 예외 안전망
            log.exception("sections refresh failed")
            self.set_status(f"섹션 로드 실패 (INTERNAL): {e}", error=True)
            return

        self._sections_by_id = {
            str(s.get("section_id") or s.get("id")): s for s in sections
        }
        opt_list = self.query_one("#sections-list", OptionList)
        opt_list.clear_options()
        if not sections:
            opt_list.add_option(Option("(섹션 없음)", disabled=True, id="__empty__"))
            self.set_status("섹션이 없습니다. whooing.com 에서 먼저 생성하세요.")
            return

        for s in sections:
            sid = str(s.get("section_id") or s.get("id"))
            title = s.get("title") or "(no title)"
            opt_list.add_option(Option(f"{title}  [dim]{sid}[/dim]", id=sid))

        self.set_status(f"섹션 {len(sections)}개 로드 완료. 선택하면 계정과목이 표시됩니다.")
        # 첫 항목을 highlight (선택은 아직 — 사용자가 enter 누르면 확정)
        opt_list.highlighted = 0
        # 첫 진입 UX 개선: 첫 섹션을 자동 활성화하여 accounts 도 미리 보여준다.
        first = sections[0]
        first_sid = str(first.get("section_id") or first.get("id"))
        first_title = first.get("title") or None
        self._activate_section(first_sid, first_title)

    # ---- accounts worker ---------------------------------------------

    @work(exclusive=True, group="accounts", name="refresh_accounts")
    async def refresh_accounts(self, section_id: str) -> None:
        try:
            raw = await self._client.list_accounts(section_id)
        except ToolError as e:
            self.set_status(f"계정과목 로드 실패 [{e.kind}] {e.message}", error=True)
            return
        except Exception as e:  # pragma: no cover
            log.exception("accounts refresh failed")
            self.set_status(f"계정과목 로드 실패 (INTERNAL): {e}", error=True)
            return

        flat = WhooingClient.flatten_accounts(raw)
        # SessionState 갱신 — id ↔ title 양방향 인덱스 빌드.
        session = self.app.session  # type: ignore[attr-defined]
        if session.section_id != section_id:
            # 사용자가 selected 직후 worker 가 끝났을 수도 있으니 재확인.
            session.set_section(section_id, self._sections_by_id.get(section_id, {}).get("title"))
        session.set_accounts(raw, flat)

        self._render_accounts_tree(section_id, raw, flat)
        self.set_status(
            f"섹션 {section_id} ({session.section_title or '?'}): 계정과목 {len(flat)}개 로드 완료."
        )

    # ---- 트리 렌더링 ---------------------------------------------------

    def _render_accounts_tree(
        self,
        section_id: str,
        raw: dict[str, Any],
        flat: list[dict[str, str]],
    ) -> None:
        title = self._sections_by_id.get(section_id, {}).get("title") or section_id
        tree = self.query_one("#accounts-tree", Tree)
        tree.reset(f"{title}  [dim]{section_id}[/dim]")
        tree.root.expand()

        # type 별 그룹 — _TYPE_ORDER 우선 + 그 외는 알파벳순 뒤에.
        seen_types = list(raw.keys())
        ordered = [t for t in _TYPE_ORDER if t in seen_types]
        ordered += sorted(t for t in seen_types if t not in _TYPE_ORDER)

        for t in ordered:
            items = raw.get(t)
            if not isinstance(items, list) or not items:
                continue
            label = _TYPE_LABEL.get(t, t)
            type_node = tree.root.add(f"[bold]{label}[/bold]  [dim]({len(items)})[/dim]", expand=True)
            for a in items:
                aid = str(a.get("account_id") or a.get("id") or "")
                name = a.get("title") or a.get("name") or "(no title)"
                if not aid:
                    continue
                type_node.add_leaf(f"{name}  [dim]{aid}[/dim]")

    # ---- option list 이벤트 -------------------------------------------

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        """사용자가 enter 로 섹션 선택."""
        sid = event.option.id
        if not sid or sid == "__empty__":
            return
        title = self._sections_by_id.get(sid, {}).get("title")
        self._activate_section(sid, title)

    def _activate_section(self, section_id: str, title: str | None) -> None:
        """SessionState 갱신 + accounts worker 트리거."""
        session = self.app.session  # type: ignore[attr-defined]
        session.set_section(section_id, title)
        self.set_status(f"섹션 {section_id} ({title or '?'}) 의 계정과목을 불러오는 중…")
        self.refresh_accounts(section_id)

    # ---- status bar ---------------------------------------------------

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.last_status = text
        bar = self.query_one("#status", Static)
        bar.update(text)
        bar.remove_class("error")
        if error:
            bar.add_class("error")
