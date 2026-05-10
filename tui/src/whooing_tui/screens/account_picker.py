"""AccountPickerScreen — `EntryEditDialog` 의 left/right 선택 모달 (트리 형).

CL #51076 의 단일 OptionList 가 모든 계정과목을 한꺼번에 펼쳐 보여줘 항목
수가 많을 때 선택이 어렵다는 사용자 피드백 (CL #51080) 으로 **카테고리
헤더 + 트리 펼침** 형태로 재작성.

레이아웃:
  ▼ 자산
      현금
      통장
  ▶ 부채
  ▶ 자본
  ▶ 수입
  ▼ 지출
      식비          ← (현재 선택)
      교통비
  ▶ 그룹

키 동작:
  ↑/↓        : 노드 이동
  Enter/Space: 카테고리 위에서는 펼침/접힘, 항목 위에서는 선택 후 dismiss
  Esc / q    : dismiss(None)

`current_id` 가 명시되면 해당 항목이 속한 카테고리만 자동 펼침 + cursor 가
그 항목 위에 위치 — 사용자가 "현재 선택" 을 즉시 확인하고 다른 카테고리
탐색 비용을 안 치르게.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, Tree

from whooing_tui.ime import bind_ko
from whooing_tui.state import SessionState

log = logging.getLogger(__name__)


# 후잉 표준 type 표시 순서 — 자산 → 부채 → 자본 → 수입 → 지출 → 그룹.
_TYPE_ORDER = ("assets", "liabilities", "capital", "income", "expenses", "group")
_TYPE_LABEL = {
    "assets": "자산",
    "liabilities": "부채",
    "capital": "자본",
    "income": "수입",
    "expenses": "지출",
    "group": "그룹",
}


class AccountPickerScreen(ModalScreen[tuple[str, str, str] | None]):
    """계정과목 선택 모달. dismiss((account_id, title, type_key) | None).

    Tree widget 으로 카테고리(branch) → 항목(leaf) 2-level 구조. leaf 의
    `data` 에 `(account_id, title, type_key)` 튜플을 저장 — `NodeSelected`
    이벤트에서 그대로 회수.
    """

    DEFAULT_CSS = """
    AccountPickerScreen {
        align: center middle;
    }
    #picker-frame {
        width: 64;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #picker-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #picker-hint {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    Tree {
        height: auto;
        max-height: 22;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        *bind_ko("q", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        session: SessionState,
        *,
        side: str,
        current_id: str | None = None,
    ) -> None:
        """`side` = "left" / "right" — 모달 타이틀 라벨용.

        `current_id` 가 주어지면 해당 항목 카테고리만 자동 펼침 + cursor 위치.
        """
        super().__init__()
        self._session = session
        self._side = side
        self._current = current_id

    def compose(self) -> ComposeResult:
        side_label = "차변 (left)" if self._side == "left" else "대변 (right)"
        with Vertical(id="picker-frame"):
            yield Static(
                f"[bold]계정과목 선택 — {side_label}[/bold]", id="picker-title",
            )
            yield Static(
                "↑/↓ 이동 / Enter 펼침·선택 / Esc 취소", id="picker-hint",
            )
            tree: Tree[tuple[str, str, str] | str] = Tree("계정과목", id="acc-tree")
            tree.show_root = False  # 가짜 루트 숨김 — 카테고리가 시각상 최상위.
            tree.guide_depth = 3
            yield tree

    def on_mount(self) -> None:
        tree = self.query_one("#acc-tree", Tree)
        # 카테고리별로 항목 그루핑.
        grouped = self._group_accounts()
        # 사용자가 곧장 cursor 가 닿을 leaf — `current_id` 와 일치하는 leaf.
        target_leaf = None
        for type_key in _TYPE_ORDER:
            entries = grouped.get(type_key) or []
            if not entries:
                continue
            label = f"{_TYPE_LABEL.get(type_key, type_key)}  ({len(entries)})"
            cat_node = tree.root.add(label, data=type_key, expand=False)
            for a in entries:
                aid = str(a.get("account_id") or "")
                title = a.get("title") or "(no title)"
                leaf_label = f"{title}  [dim]{aid}[/dim]"
                leaf = cat_node.add_leaf(
                    leaf_label, data=(aid, title, type_key),
                )
                if aid and aid == self._current:
                    target_leaf = leaf
                    cat_node.expand()
        # 현재 선택이 없거나 못 찾으면 첫 카테고리만 펼침 (시작점 가시화).
        if target_leaf is None:
            for child in tree.root.children:
                if child.children:
                    child.expand()
                    break
        else:
            # cursor 를 target leaf 로 — `select_node` 는 `NodeSelected`
            # 이벤트를 발사해 모달이 즉시 dismiss 되므로 `move_cursor`.
            tree.move_cursor(target_leaf)
        tree.focus()

    # ---- helpers ------------------------------------------------------

    def _group_accounts(self) -> dict[str, list[dict[str, Any]]]:
        """{type_key: [account, ...]} — 같은 type 안에서는 받은 순서 유지."""
        out: dict[str, list[dict[str, Any]]] = {t: [] for t in _TYPE_ORDER}
        for a in self._session.accounts_flat:
            tk = a.get("type") or ""
            if tk in out:
                out[tk].append(a)
            else:
                # 알려지지 않은 type 은 group 처럼 취급.
                out.setdefault(tk, []).append(a)
        return out

    # ---- actions ------------------------------------------------------

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """leaf 가 선택됐으면 dismiss. branch 면 noop — Tree 의 `auto_expand`
        가 이미 토글을 처리한다 (Textual 8.2.5 의 default).

        주의 (CL #51087 회귀): 이전에는 본 핸들러가 branch 위에서도
        `node.expand()` / `node.collapse()` 를 명시적으로 호출했는데,
        auto_expand 가 먼저 토글한 뒤 본 핸들러가 다시 토글해 결과적으로
        원래 상태로 복귀하는 버그가 있었다 — 사용자가 Enter 를 눌러도
        카테고리가 안 펼쳐지는 것처럼 보임.
        """
        node = event.node
        data = node.data
        # leaf 는 data 가 (id, title, type) 튜플. branch 는 type_key 문자열.
        if isinstance(data, tuple) and len(data) == 3:
            self.dismiss(data)
