"""Textual 화면 (Screens) 모음.

각 화면은 자체 keybinding 과 lifecycle 을 가진다. 공유 상태는 App 의
SessionState 를 통해서만 주고받는다 — 화면 사이에 직접 인스턴스
참조를 보관하지 않는다.
"""

from whooing_tui.screens.accounts import AccountEditDialog, AccountsScreen
from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen
from whooing_tui.screens.dashboard import DashboardScreen
# CL #51137+ (H1): annotator.py / AnnotatorModal 제거 — EntryEditDialog 가
# memo + 해시태그 모두 커버. parse_hashtags_input 는 edit_entry.py 에 동일
# 구현 존재해 그쪽에서 re-export (후방 호환).
from whooing_tui.screens.edit_entry import EntryEditDialog, parse_hashtags_input
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.help import HelpModal
from whooing_tui.screens.sections import SectionPickerScreen
from whooing_tui.screens.statement_import import (
    PasswordModal,
    StatementImportScreen,
)
from whooing_tui.screens.tag_management import TagManagementScreen

__all__ = [
    # 초기 화면 (v0.8.x ~ — CL #51023 부터 EntriesScreen 이 entry point)
    "EntriesScreen",
    # 옵션 화면들
    "SectionPickerScreen",
    "AccountsScreen",
    "AccountEditDialog",
    "EntryEditDialog",
    "HelpModal",
    "AttachmentBrowserScreen",
    "DashboardScreen",
    "PasswordModal",
    "StatementImportScreen",
    "TagManagementScreen",
    "parse_hashtags_input",
]
