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

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, Tree


class _HighlightOnClickTree(Tree):
    """CL #52929+: mouse 단일 클릭은 *highlight 만*, 실제 선택은 Enter 키 또는
    더블 클릭. 종전 Tree 는 click 으로 즉시 `NodeSelected` 발사 → 디렉토리
    탐색 의도 없이 잘못 누르면 picker 가 닫혀 사용자가 당황.

    토글 영역 (펼침/접힘 화살표) 클릭은 그대로 — mouse 친화.
    """

    async def _on_click(self, event: events.Click) -> None:
        async with self.lock:
            meta = event.style.meta
            if "line" in meta:
                cursor_line = meta["line"]
                if meta.get("toggle", False):
                    node = self.get_node_at_line(cursor_line)
                    if node is not None:
                        self._toggle_node(node)
                else:
                    self.cursor_line = cursor_line
                    # 더블 클릭 = 명시적 선택 (Enter 동등).
                    if event.chain >= 2:
                        await self.run_action("select_cursor")

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
        /* CL #51120+: 좁은 터미널 대응. */
        width: 95%;
        max-width: 64;
        min-width: 30;
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
    /* CL #52906+: caller (예: 카드 명세서 import) 가 picker 의 *맥락* 을
       사용자에게 설명할 수 있도록 한 줄 subtitle. 미지정 시 hidden. */
    #picker-purpose {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        text-align: center;
    }
    #picker-purpose.hidden {
        display: none;
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
        # CL #51096+: 표준 트리 UX — ←/→ 로 카테고리 펼침/접힘 + 부모/첫
        # 자식 이동. priority=True 로 Tree 의 default cursor 동작 (없음)
        # 보다 우선.
        Binding("right", "tree_expand_or_descend", "펼침/자식", show=True, priority=True),
        Binding("left", "tree_collapse_or_ascend", "접힘/부모", show=True, priority=True),
    ]

    def __init__(
        self,
        session: SessionState,
        *,
        side: str,
        current_id: str | None = None,
        purpose: str | None = None,
        default_expanded_type: str | None = None,
    ) -> None:
        """`side` = "left" / "right" — 모달 타이틀 라벨용.

        `current_id` 가 주어지면 해당 항목 카테고리만 자동 펼침 + cursor 위치.
        `purpose` (CL #52906+) 가 주어지면 제목 아래 한 줄 안내로 표시.
        `default_expanded_type` (CL #52929+): `current_id` 매칭이 없을 때 *기본*
        으로 펼칠 카테고리 key (예: 카드 명세서 import 의 2단계는 "liabilities").
        지정 안 하면 첫 비어있지 않은 카테고리.
        """
        super().__init__()
        self._session = session
        self._side = side
        self._current = current_id
        self._purpose = purpose
        self._default_expanded_type = default_expanded_type

    def compose(self) -> ComposeResult:
        side_label = "차변 (left)" if self._side == "left" else "대변 (right)"
        with Vertical(id="picker-frame"):
            yield Static(
                f"[bold]계정과목 선택 — {side_label}[/bold]", id="picker-title",
            )
            # CL #52906+: 맥락 안내 — caller 가 지정 안 하면 hidden.
            purpose_static = Static(
                self._purpose or "",
                id="picker-purpose",
            )
            if not self._purpose:
                purpose_static.add_class("hidden")
            yield purpose_static
            yield Static(
                "↑/↓ 이동 · Enter / 더블클릭=선택 · 클릭=하이라이트만 · "
                "Esc 취소",
                id="picker-hint",
            )
            tree: Tree[tuple[str, str, str] | str] = _HighlightOnClickTree(
                "계정과목", id="acc-tree",
            )
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
        # 현재 선택이 없거나 못 찾으면:
        #   1. CL #52929+: `default_expanded_type` 지정돼있고 그 카테고리에
        #      항목이 있으면 그것을 펼침 (예: 카드 명세서 → "liabilities").
        #   2. 그 외엔 첫 비어있지 않은 카테고리.
        if target_leaf is None:
            preferred = None
            if self._default_expanded_type:
                for child in tree.root.children:
                    if (
                        child.data == self._default_expanded_type
                        and child.children
                    ):
                        preferred = child
                        break
            if preferred is not None:
                preferred.expand()
                # cursor 도 그 카테고리로 — 사용자가 즉시 ↓ 로 항목 진입.
                tree.move_cursor(preferred)
            else:
                for child in tree.root.children:
                    if child.children:
                        child.expand()
                        break
        else:
            # cursor 를 target leaf 로 — `select_node` 는 `NodeSelected`
            # 이벤트를 발사해 모달이 즉시 dismiss 되므로 `move_cursor`.
            # 다만 mount 직후엔 Tree 의 `_tree_lines` 가 아직 layout 안
            # 된 상태일 수 있어 `node._line` 이 -1 → cursor 가 첫 가시 노드
            # 로 떨어지는 회귀 (CL #51096 발견). 한 frame 미루기.
            self.call_after_refresh(tree.move_cursor, target_leaf)
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

    def action_tree_expand_or_descend(self) -> None:
        """→ : 표준 트리 UX. 동작 표는 다음과 같다.

        | 현재 위치       | →                            |
        | -------------- | ----------------------------- |
        | 접힌 카테고리    | 펼침 (cursor 유지)             |
        | 펼친 카테고리    | 첫 자식 (leaf) 으로 cursor 이동  |
        | leaf            | noop                          |
        """
        tree = self.query_one("#acc-tree", Tree)
        node = tree.cursor_node
        if node is None or node is tree.root:
            return
        if node.allow_expand:
            if not node.is_expanded:
                node.expand()
                return
            # 펼친 상태 — 첫 자식으로 cursor 이동.
            children = list(node.children)
            if children:
                tree.move_cursor(children[0])

    def action_tree_collapse_or_ascend(self) -> None:
        """← : 표준 트리 UX. 동작 표는 다음과 같다.

        | 현재 위치          | ←                              |
        | ----------------- | ------------------------------ |
        | 펼친 카테고리       | 접힘 (cursor 유지)              |
        | 접힌 카테고리       | noop (이미 최상위 — 가짜 root 숨김) |
        | leaf              | 부모 (카테고리) 로 cursor 이동      |
        """
        tree = self.query_one("#acc-tree", Tree)
        node = tree.cursor_node
        if node is None or node is tree.root:
            return
        if node.allow_expand:
            if node.is_expanded:
                node.collapse()
            # 접힌 카테고리에서 ← 는 noop (root 가 숨김이라 부모로 갈 곳이
            # 없다 — 사용자에게 시각상 변화 없음).
            return
        # leaf — 부모 카테고리로 cursor 이동.
        parent = node.parent
        if parent is not None and parent is not tree.root:
            tree.move_cursor(parent)

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
