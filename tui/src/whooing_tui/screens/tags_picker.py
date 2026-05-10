"""TagsPickerScreen — `EntryEditDialog` 의 tags 입력 보조 모달 (CL #51080+).

tags Input 위에서 Enter 를 누르면 본 모달이 push 된다. 사용자가 *기존*
태그 중 하나를 골라 추가하거나, *새* 태그를 만들 수 있게 도와준다.

사용자 흐름:
  1. 모달이 열리면 두 섹션이 보인다:
     - **추천**: 현재 거래의 item / memo 텍스트에 substring 으로 매칭되는
       기존 태그 (사용자 입력의 의미적 hint).
     - **자주 쓰는 태그**: 모든 기존 태그를 사용 빈도 (count) 내림차순.
  2. 입력란에 타이핑하면 두 섹션 모두 prefix / substring 매칭으로 필터.
     맨 위에 항상 `[+ 새 태그 만들기: <input>]` 옵션이 노출 (입력이 비어
     있으면 숨김).
  3. ↑/↓ 로 옵션 이동, Enter 로 선택. 입력란의 Enter 도 동일하게 OptionList
     의 highlighted 옵션 → 선택 (선택지가 하나도 없는데 입력이 있다면 그
     입력값으로 새 태그 생성).

dismiss 결과: 선택된 tag string (한 개) 또는 None.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from whooing_tui.ime import bind_ko

log = logging.getLogger(__name__)


# Option ID prefix — OptionList 의 모든 옵션에 stable id 가 필요.
_PREFIX_NEW = "new::"            # `+ 새 태그 만들기: foo` 의 id = `new::foo`
_PREFIX_EXISTING = "tag::"       # 기존 태그 — id = `tag::식비`


def _tokenize_for_recommend(text: str) -> list[str]:
    """item / memo 텍스트에서 추천 후보 토큰 추출.

    공백 / 구두점 / 괄호 / `,` 등을 분리자로 해 2글자 이상 토큰만 보존.
    중복 제거 (insertion order). 예: `"스타벅스(아메리카노) 점심"` →
    `["스타벅스", "아메리카노", "점심"]`.
    """
    if not text:
        return []
    tokens = re.split(r"[\s\(\)\[\]\{\},.\-_/·]+", text)
    seen: list[str] = []
    for t in tokens:
        t = t.strip()
        if len(t) >= 2 and t not in seen:
            seen.append(t)
    return seen


def recommend_tags(
    *, item: str, memo: str, existing: dict[str, int],
) -> list[str]:
    """item / memo 에 substring 으로 들어있는 기존 태그를 추천.

    `existing` 은 `{tag: count}` 사전 (사용 빈도). 정렬 우선순위:
      1) item / memo 본문에 substring 으로 포함되는 태그 (가장 강한 매칭).
      2) 본문 토큰과 정확히 일치하는 태그 (보강 — substring 와 합집합).
      3) 동일 score 안에서 사용 빈도 내림차순.
    """
    if not existing:
        return []
    body = f"{item or ''}  {memo or ''}"
    tokens = set(_tokenize_for_recommend(body))
    scored: list[tuple[int, int, str]] = []  # (-score, -count, tag) — 정렬용
    for tag, count in existing.items():
        score = 0
        if tag in tokens:
            score += 2
        if tag and tag in body:
            score += 1
        if score > 0:
            scored.append((-score, -count, tag))
    scored.sort()
    return [t for _, _, t in scored]


def filter_tags(
    query: str, tags: Iterable[str],
) -> list[str]:
    """`query` 가 포함된 태그만 (대소문자 무시) — 정렬 보존.

    빈 query 면 입력 그대로. prefix 우선, 그 다음 substring (안정 정렬).
    """
    q = (query or "").strip().lower()
    if not q:
        return list(tags)
    prefix: list[str] = []
    sub: list[str] = []
    for t in tags:
        tl = t.lower()
        if tl.startswith(q):
            prefix.append(t)
        elif q in tl:
            sub.append(t)
    return prefix + sub


class TagsPickerScreen(ModalScreen[str | None]):
    """해시태그 1개를 선택/생성. dismiss(tag | None)."""

    DEFAULT_CSS = """
    TagsPickerScreen {
        align: center middle;
    }
    #tagpick-frame {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #tagpick-title {
        height: 1;
        content-align: center middle;
        color: $accent;
    }
    #tagpick-hint {
        height: 1;
        content-align: center middle;
        color: $text-muted;
    }
    #tagpick-input {
        margin-top: 1;
    }
    OptionList {
        height: auto;
        max-height: 18;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(
        self,
        *,
        item: str = "",
        memo: str = "",
        existing: dict[str, int] | None = None,
        already_selected: list[str] | None = None,
    ) -> None:
        """추천에 쓸 item/memo + 기존 태그 사전 + 이미 입력란에 들어가 있는 태그.

        `already_selected` 는 다시 추천하지 않도록 옵션에서 제외용.
        """
        super().__init__()
        self._item = item or ""
        self._memo = memo or ""
        self._existing: dict[str, int] = dict(existing or {})
        self._already: set[str] = {t.strip() for t in (already_selected or []) if t.strip()}

    # ---- compose / mount ----------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="tagpick-frame"):
            yield Static("[bold]태그 선택 / 추가[/bold]", id="tagpick-title")
            yield Static(
                "타이핑으로 검색 / Enter 선택 또는 새 태그 / Esc 취소",
                id="tagpick-hint",
            )
            yield Input(placeholder="새 태그 또는 검색어", id="tagpick-input")
            yield OptionList(id="tagpick-list")

    def on_mount(self) -> None:
        self._refresh_list()
        self.query_one("#tagpick-input", Input).focus()

    # ---- list 렌더 -----------------------------------------------------

    def _refresh_list(self) -> None:
        """현재 입력값 + 추천 + 자주 쓰는 태그를 OptionList 에 다시 그림.

        섹션 구성:
          [+ 새 태그 만들기: <input>]   ← input 이 비어있지 않을 때만
          ─── (separator)
          추천 (item/memo 매칭)         ← item/memo 가 있을 때만
            <tag1>, <tag2>, ...
          ─── (separator)
          자주 쓰는 태그
            <tag (count)>, ...
        """
        opt = self.query_one("#tagpick-list", OptionList)
        opt.clear_options()
        query = self.query_one("#tagpick-input", Input).value.strip()

        # 0) 새 태그 옵션 — 입력이 있고 기존에 정확히 같은 이름이 없으며
        #    이미 선택된 토큰도 아닐 때만.
        if query:
            normalized = query.lstrip("#").strip()
            if normalized and normalized not in self._existing and normalized not in self._already:
                opt.add_option(
                    Option(
                        f"[bold]+ 새 태그 만들기:[/bold] {normalized}",
                        id=f"{_PREFIX_NEW}{normalized}",
                    ),
                )
                opt.add_option(Option("", disabled=True))

        # 1) 추천 — item/memo 매칭.
        rec_all = recommend_tags(
            item=self._item, memo=self._memo, existing=self._existing,
        )
        rec = [t for t in filter_tags(query, rec_all) if t not in self._already]
        if rec:
            opt.add_option(Option("[dim]── 추천 (item/memo) ★ ──[/dim]", disabled=True))
            for t in rec:
                count = self._existing.get(t, 0)
                opt.add_option(
                    Option(f"  {t}  [dim]({count})[/dim]", id=f"{_PREFIX_EXISTING}{t}"),
                )
            opt.add_option(Option("", disabled=True))

        # 2) 자주 쓰는 태그 — 사용 빈도 내림차순.
        rec_set = set(rec)
        all_sorted = sorted(self._existing.items(), key=lambda kv: (-kv[1], kv[0]))
        rest = [
            t for t, _ in all_sorted
            if t not in rec_set and t not in self._already
        ]
        rest = filter_tags(query, rest)
        if rest:
            opt.add_option(
                Option("[dim]── 자주 쓰는 태그 ──[/dim]", disabled=True),
            )
            for t in rest:
                count = self._existing.get(t, 0)
                opt.add_option(
                    Option(f"  {t}  [dim]({count})[/dim]", id=f"{_PREFIX_EXISTING}{t}"),
                )

        # 첫 번째 *선택 가능* 옵션을 highlighted 로.
        for i in range(opt.option_count):
            o = opt.get_option_at_index(i)
            if not o.disabled:
                opt.highlighted = i
                break

    # ---- events --------------------------------------------------------

    @on(Input.Changed, "#tagpick-input")
    def _on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_list()

    @on(Input.Submitted, "#tagpick-input")
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        """Input 의 Enter — OptionList 의 highlighted 항목 선택.

        highlighted 옵션이 없거나 disabled 면 입력값 자체를 새 태그로 만든다.
        """
        opt = self.query_one("#tagpick-list", OptionList)
        idx = opt.highlighted
        if idx is not None:
            try:
                option = opt.get_option_at_index(idx)
                if not option.disabled and option.id:
                    self._dismiss_with_option_id(option.id)
                    return
            except Exception:  # pragma: no cover
                pass
        # fallback — 입력값으로 새 태그 (있다면).
        raw = (event.value or "").strip().lstrip("#").strip()
        if raw:
            self.dismiss(raw)
        # 빈 입력 + 옵션 없음 → noop (사용자가 다시 입력하도록).

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id
        if oid:
            self._dismiss_with_option_id(oid)

    def _dismiss_with_option_id(self, oid: str) -> None:
        if oid.startswith(_PREFIX_NEW):
            self.dismiss(oid[len(_PREFIX_NEW):])
        elif oid.startswith(_PREFIX_EXISTING):
            self.dismiss(oid[len(_PREFIX_EXISTING):])

    def action_cancel(self) -> None:
        self.dismiss(None)
