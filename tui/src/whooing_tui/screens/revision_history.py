"""RevisionHistoryScreen — 한 거래의 전체 변경 이력 + 임의 버전 되돌리기.

시나리오 11. dismiss 값:
  - `("revert", revision_no)` — 그 버전으로 되돌리기 요청 (caller 가 수행).
  - `None` — 닫기.

후잉 mutation 은 하지 않는다 — EntriesScreen 워커가 결과를 받아 수행.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from whooing_core import revisions as core_rev

_OP_LABEL = {
    core_rev.OP_CREATE: "생성",
    core_rev.OP_EDIT: "수정",
    core_rev.OP_DELETE: "삭제",
    core_rev.OP_RESTORE: "복원",
    core_rev.OP_REVERT: "되돌림",
    core_rev.OP_EXTERNAL: "외부변경",
}


def _fmt_money(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v if v is not None else "")


class RevisionHistoryScreen(ModalScreen["tuple[str, int] | None"]):
    BINDINGS = [
        Binding("escape", "cancel", "닫기"),
        Binding("q", "cancel", "닫기"),
        Binding("enter", "revert", "이 버전으로 되돌리기"),
    ]

    DEFAULT_CSS = """
    RevisionHistoryScreen { align: center middle; }
    #rev-frame {
        width: 92%; max-width: 130; min-width: 44;
        height: 82%; max-height: 42; min-height: 10;
        background: $panel; border: solid $primary; padding: 0 1;
    }
    #rev-title { color: $accent; text-style: bold; height: 1; }
    #rev-hint { color: $text-muted; height: 1; }
    #rev-list { height: 1fr; }
    """

    def __init__(
        self, revisions: list[dict[str, Any]], *, logical_id: str,
    ) -> None:
        super().__init__()
        # 최신 버전이 위로 오도록 역순.
        self._revisions = list(reversed(revisions))
        self._logical_id = logical_id

    def compose(self) -> ComposeResult:
        with Container(id="rev-frame"):
            yield Static(
                f"수정 이력 — logical {self._logical_id} "
                f"({len(self._revisions)}개 버전)",
                id="rev-title",
            )
            yield Static(
                "Enter 선택 버전으로 되돌리기 · Esc 닫기", id="rev-hint",
            )
            options: list[Option] = []
            prev_snaps = {
                r["revision_no"]: core_rev.snapshot_fields(r)
                for r in self._revisions
            }
            # diff 요약: 각 버전 vs 바로 이전(revision_no-1) 버전.
            by_no = {r["revision_no"]: r for r in self._revisions}
            for r in self._revisions:
                no = r["revision_no"]
                op = _OP_LABEL.get(r.get("op"), r.get("op"))
                ts = str(r.get("created_at") or "")[:16].replace("T", " ")
                prev = by_no.get(no - 1)
                summary = r.get("note") or ""
                if not summary and prev is not None:
                    summary = core_rev.summarize_diff(
                        core_rev.diff(
                            core_rev.snapshot_fields(prev), prev_snaps[no],
                        )
                    )
                if not summary:
                    summary = (
                        f"{_fmt_money(r.get('money'))} · {r.get('item') or ''}"
                    )
                label = f"v{no:<3} {op:<6} {ts}   {summary[:48]}"
                options.append(Option(label, id=str(no)))
            yield OptionList(*options, id="rev-list")

    def on_mount(self) -> None:
        try:
            self.query_one("#rev-list", OptionList).focus()
        except Exception:  # pragma: no cover
            pass

    def action_revert(self) -> None:
        try:
            ol = self.query_one("#rev-list", OptionList)
            if ol.highlighted is None:
                return
            oid = ol.get_option_at_index(ol.highlighted).id
        except Exception:  # pragma: no cover
            return
        if oid is not None:
            self.dismiss(("revert", int(oid)))

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["RevisionHistoryScreen"]
