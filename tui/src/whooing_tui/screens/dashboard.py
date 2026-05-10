"""DashboardScreen — 한눈 보기.

최근 import 통계 / annotation 카운트 / attachment 합계 / db meta.
모두 mode=ro SELECT — wrapper read 와 충돌 X.

진입: 'D' (대시보드 단축키 — app.py 에 binding 추가 후).
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from whooing_tui import data as tui_data

log = logging.getLogger(__name__)


def gather_stats(*, section_id: str | None = None) -> dict[str, Any]:
    """통계 한 번에. db 없으면 빈 결과 (각 키 = 0/[]).

    CL #51133+ (H2): `section_id` 명시 시 해당 섹션의 hashtag / attachment 만
    집계 — cross-section 통계 오염 방지. None 이면 종전 동작 (모든 섹션 합).
    """
    out: dict[str, Any] = {
        "schema_version": None,
        "db_path": str(tui_data.db_path()),
        "section_id": section_id,
        "import_total": 0,
        "import_by_status": {},
        "annotation_count": 0,
        "annotation_with_memo": 0,
        "top_hashtags": [],
        "attachment_count": 0,
        "attachment_total_bytes": 0,
        "attachment_unique_files": 0,
    }
    out["schema_version"] = tui_data.schema_version()
    if out["schema_version"] is None:
        return out

    try:
        with tui_data.open_ro() as conn:
            # imports — section 필터.
            if section_id is None:
                imp_rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM statement_import_log "
                    "GROUP BY status"
                )
            else:
                imp_rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM statement_import_log "
                    "WHERE section_id = ? GROUP BY status",
                    (section_id,),
                )
            for r in imp_rows:
                out["import_by_status"][r["status"]] = r["n"]
                out["import_total"] += r["n"]
            # annotations
            if section_id is None:
                ann_row = conn.execute(
                    "SELECT COUNT(*) AS n, COUNT(note) AS m FROM entry_annotations"
                ).fetchone()
            else:
                ann_row = conn.execute(
                    "SELECT COUNT(*) AS n, COUNT(note) AS m FROM entry_annotations "
                    "WHERE section_id = ?",
                    (section_id,),
                ).fetchone()
            out["annotation_count"] = ann_row["n"]
            out["annotation_with_memo"] = ann_row["m"]
            # top hashtags — section 필터 (CL #51133+ H2).
            if section_id is None:
                tag_rows = conn.execute(
                    "SELECT tag, COUNT(*) AS n FROM entry_hashtags "
                    "GROUP BY tag ORDER BY n DESC LIMIT 10"
                ).fetchall()
            else:
                tag_rows = conn.execute(
                    "SELECT tag, COUNT(*) AS n FROM entry_hashtags "
                    "WHERE section_id = ? GROUP BY tag ORDER BY n DESC LIMIT 10",
                    (section_id,),
                ).fetchall()
            out["top_hashtags"] = [(r["tag"], r["n"]) for r in tag_rows]
            # attachments — section 필터.
            if section_id is None:
                att_row = conn.execute(
                    "SELECT COUNT(*) AS n, COUNT(DISTINCT file_sha256) AS u, "
                    "COALESCE(SUM(file_size_bytes), 0) AS bytes "
                    "FROM entry_attachments"
                ).fetchone()
            else:
                att_row = conn.execute(
                    "SELECT COUNT(*) AS n, COUNT(DISTINCT file_sha256) AS u, "
                    "COALESCE(SUM(file_size_bytes), 0) AS bytes "
                    "FROM entry_attachments WHERE section_id = ?",
                    (section_id,),
                ).fetchone()
            out["attachment_count"] = att_row["n"]
            out["attachment_unique_files"] = att_row["u"]
            out["attachment_total_bytes"] = att_row["bytes"]
    except Exception as ex:
        log.exception("dashboard query failed: %s", ex)
    return out


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 ** 3):.2f} GB"


def render_dashboard(stats: dict[str, Any]) -> str:
    """plain-text rendering (Static 위젯에 set)."""
    lines: list[str] = []
    lines.append(f"  📁 db          {stats['db_path']}")
    sv = stats["schema_version"]
    lines.append(
        f"  ⚙️  schema       v{sv}" if sv is not None
        else "  ⚙️  schema       (db 미생성 — 첫 작업 시 자동 init)"
    )
    lines.append("")
    lines.append("  📥 statement import")
    if stats["import_total"]:
        for status, n in stats["import_by_status"].items():
            lines.append(f"      {status:18s} {n}")
        lines.append(f"      {'전체':18s} {stats['import_total']}")
    else:
        lines.append("      (아직 없음)")
    lines.append("")
    lines.append("  📝 annotations")
    lines.append(f"      annotated entries  {stats['annotation_count']}")
    lines.append(f"      with memo          {stats['annotation_with_memo']}")
    if stats["top_hashtags"]:
        lines.append("      top hashtags:")
        for tag, n in stats["top_hashtags"]:
            lines.append(f"        #{tag:20s} ({n})")
    lines.append("")
    lines.append("  📎 attachments")
    lines.append(f"      total rows         {stats['attachment_count']}")
    lines.append(f"      unique files       {stats['attachment_unique_files']}")
    lines.append(f"      total size         {_fmt_bytes(stats['attachment_total_bytes'])}")
    return "\n".join(lines)


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "뒤로"),
        Binding("r", "refresh", "새로고침"),
    ]

    DEFAULT_CSS = """
    DashboardScreen {
        layout: vertical;
    }
    #dash_panel {
        padding: 2;
        background: $boost;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Dashboard 로딩 중...", id="dash_panel")
        yield Footer()

    def on_mount(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        stats = gather_stats()
        self.query_one("#dash_panel", Static).update(render_dashboard(stats))

    def action_back(self) -> None:
        self.app.pop_screen()
