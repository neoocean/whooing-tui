"""Textual 화면 (Screens) 모음.

각 화면은 자체 keybinding 과 lifecycle 을 가진다. 공유 상태는 App 의
SessionState 를 통해서만 주고받는다 — 화면 사이에 직접 인스턴스
참조를 보관하지 않는다.
"""

from whooing_tui.screens.annotator import AnnotatorModal, parse_hashtags_input
from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen
from whooing_tui.screens.dashboard import DashboardScreen
from whooing_tui.screens.edit_entry import EntryEditDialog
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.help import HelpModal
from whooing_tui.screens.home import HomeScreen
from whooing_tui.screens.statement_import import (
    PasswordModal,
    StatementImportScreen,
)

__all__ = [
    # v0.4.x
    "EntriesScreen",
    "EntryEditDialog",
    "HelpModal",
    "HomeScreen",
    # v0.6.0 — statement import / annotator / attachment / dashboard
    "AnnotatorModal",
    "AttachmentBrowserScreen",
    "DashboardScreen",
    "PasswordModal",
    "StatementImportScreen",
    "parse_hashtags_input",
]
