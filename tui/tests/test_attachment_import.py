"""AttachmentBrowserScreen — 후잉 서버 첨부 'i' 가져오기 통합.

server_attachments.download 를 monkeypatch 해 실제 네트워크 없이, 서버 첨부
메타 → 로컬 첨부(sqlite + store) 저장 흐름을 검증.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from whooing_tui import data as tui_data
from whooing_tui import server_attachments as srv
from whooing_tui.screens.attachment_browser import (
    AttachmentBrowserScreen,
    list_for,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    tui_data.init_shared_schema()
    return tmp_path


class _Host(App):
    def __init__(self, screen: AttachmentBrowserScreen) -> None:
        super().__init__()
        self._scr = screen

    def compose(self) -> ComposeResult:
        yield Static("base")

    def on_mount(self) -> None:
        self.push_screen(self._scr)


def _meta(uuid="u1", filename="server.pdf"):
    return {"uuid": uuid, "filename": filename,
            "src": f"https://static.whooing.com/get/{uuid}",
            "mimeType": "application/pdf", "size": 0.01}


async def _settle(pilot, n=10):
    for _ in range(n):
        await pilot.pause()


async def test_import_server_attachment(isolated, monkeypatch):
    async def fake_download(src, *, token=None, timeout=30.0):
        return b"%PDF-fake-from-server"
    monkeypatch.setattr(srv, "download", fake_download)

    scr = AttachmentBrowserScreen(
        "e_test", section_id="s9046", server_attachments=[_meta()],
    )
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        assert list_for("e_test", section_id="s9046") == []
        scr.action_import_server()
        await _settle(pilot)
        rows = list_for("e_test", section_id="s9046")
        assert len(rows) == 1
        assert rows[0]["original_filename"] == "server.pdf"

        # 재가져오기 → 같은 파일명 이미 로컬 → pending 0 → 새 row 없음
        scr.action_import_server()
        await _settle(pilot)
        assert len(list_for("e_test", section_id="s9046")) == 1


async def test_import_no_server_attachments(isolated, monkeypatch):
    called = False

    async def fake_download(src, *, token=None, timeout=30.0):
        nonlocal called
        called = True
        return b"x"
    monkeypatch.setattr(srv, "download", fake_download)

    scr = AttachmentBrowserScreen("e_none", section_id="s9046", server_attachments=[])
    app = _Host(scr)
    async with app.run_test() as pilot:
        await _settle(pilot)
        scr.action_import_server()
        await _settle(pilot)
        assert called is False
        assert list_for("e_none", section_id="s9046") == []
