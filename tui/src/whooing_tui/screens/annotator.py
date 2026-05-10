"""AnnotatorModal — 거래 ID 의 로컬 메모 + 해시태그 편집.

후잉 자체 memo 한 줄로는 부족한 사용자 컨텍스트를 보관 (출장명 / 영수증
내용 / 카테고리 분류 등). SQLite 의 entry_annotations + entry_hashtags
테이블에 저장 — wrapper 가 read-only 로 audit 응답에 자동 부착.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea

from whooing_core import db as core_db

from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


def parse_hashtags_input(s: str) -> list[str]:
    """'#식비 #출장,서울' → ['식비', '출장', '서울']. 공백/콤마 분리, # 제거."""
    if not s:
        return []
    raw = s.replace(",", " ").split()
    out: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        t = tok.strip().lstrip("#")
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


class AnnotatorModal(ModalScreen[dict | None]):
    """entry_id 의 메모/태그 modal. dismiss 시 saved dict 또는 None."""

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        Binding("ctrl+s", "save", "저장"),
    ]

    DEFAULT_CSS = """
    #annot_box {
        width: 70;
        height: auto;
        background: $panel;
        border: thick $primary;
        padding: 1;
    }
    #annot_memo {
        height: 6;
        margin: 1 0;
    }
    #annot_tags {
        margin: 1 0;
    }
    """

    def __init__(
        self,
        entry_id: str,
        section_id: str | None = None,
        initial_note: str | None = None,
        initial_hashtags: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.entry_id = entry_id
        self.section_id = section_id
        self.initial_note = initial_note or ""
        self.initial_hashtags = list(initial_hashtags or [])

    def compose(self) -> ComposeResult:
        with Container(id="annot_box"):
            yield Label(f"Entry {self.entry_id} — 로컬 annotation")
            yield Label("메모 (자유 길이)")
            yield TextArea(self.initial_note, id="annot_memo")
            yield Label("해시태그 (공백 또는 콤마 구분, # 자동)")
            yield Input(
                value=" ".join(f"#{t}" for t in self.initial_hashtags),
                placeholder="#식비 #카페 #서울출장",
                id="annot_tags",
            )
            with Horizontal():
                yield Button("저장 (Ctrl+S)", id="annot_save", variant="primary")
                yield Button("취소", id="annot_cancel")

    def on_mount(self) -> None:
        self.query_one("#annot_memo", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "annot_save":
            self.action_save()
        else:
            self.action_cancel()

    def action_save(self) -> None:
        memo = self.query_one("#annot_memo", TextArea).text
        tags_raw = self.query_one("#annot_tags", Input).value
        tags = parse_hashtags_input(tags_raw)

        try:
            with tui_data.open_rw() as conn:
                core_db.upsert_annotation(
                    conn,
                    entry_id=self.entry_id,
                    section_id=self.section_id,
                    note=memo if memo.strip() else None,
                )
                normalized = core_db.set_hashtags(conn, self.entry_id, tags)
        except Exception as ex:
            log.exception("annotation save failed: %s", ex)
            self.notify(f"저장 실패: {ex}", severity="error")
            return

        self.dismiss({
            "entry_id": self.entry_id,
            "saved": True,
            "note": memo,
            "hashtags": normalized,
        })

    def action_cancel(self) -> None:
        self.dismiss(None)


def load_existing_annotation(entry_id: str) -> tuple[str | None, list[str]]:
    """db 에서 (note, hashtags) — 기존 데이터 fetch (annotator 진입 시 사용)."""
    try:
        with tui_data.open_ro() as conn:
            data_map = core_db.get_annotations_for(conn, [entry_id])
    except FileNotFoundError:
        return None, []
    a = data_map.get(entry_id, {})
    return a.get("note"), list(a.get("hashtags", []))
