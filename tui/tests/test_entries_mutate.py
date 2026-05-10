"""EntriesScreen mutation 액션 통합 테스트.

EntryEditDialog 를 실제로 띄워 사용자가 입력한 EntryDraft 가 client 의
create_entry / update_entry / delete_entry 로 위임되는 흐름을 검증.

dialog 자체의 키 입력 시뮬은 textual ModalScreen 의 lifecycle 이 복잡해
직접 dismiss 호출로 단축한다 — dialog 폼 검증은 별도 unit test 가 커버.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import DataTable

from whooing_tui.app import WhooingTuiApp
from whooing_tui.models import ToolError
from whooing_tui.screens.edit_entry import (
    ConfirmModal, EntryDraft, EntryEditDialog,
)
from whooing_tui.screens.entries import EntriesScreen


class FakeClient:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x21", "title": "교통비"},
            ],
        }
        self._entries = entries if entries is not None else [
            {
                "entry_id": "e1", "entry_date": "20260510",
                "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
                "item": "스타벅스",
            },
        ]
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.create_error: ToolError | None = None
        self.update_error: ToolError | None = None
        self.delete_error: ToolError | None = None

    async def list_sections(self) -> list[dict[str, Any]]:
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def create_entry(self, **kwargs) -> dict[str, Any]:
        if self.create_error is not None:
            raise self.create_error
        self.create_calls.append(kwargs)
        new = {
            "entry_id": f"new-{len(self.create_calls)}",
            "entry_date": kwargs["entry_date"],
            "money": kwargs["money"],
            "l_account_id": kwargs["l_account_id"],
            "r_account_id": kwargs["r_account_id"],
            "item": kwargs.get("item", ""),
            "memo": kwargs.get("memo", ""),
        }
        self._entries.append(new)
        return new

    async def update_entry(self, **kwargs) -> dict[str, Any]:
        if self.update_error is not None:
            raise self.update_error
        self.update_calls.append(kwargs)
        for e in self._entries:
            if e.get("entry_id") == kwargs["entry_id"]:
                for k in ("money", "l_account_id", "r_account_id", "item", "memo", "entry_date"):
                    if k in kwargs and kwargs[k] is not None:
                        e[k] = kwargs[k]
                return e
        return {}

    async def delete_entry(self, *, section_id, entry_id) -> dict[str, Any]:
        if self.delete_error is not None:
            raise self.delete_error
        self.delete_calls.append({"section_id": section_id, "entry_id": entry_id})
        self._entries = [e for e in self._entries if e.get("entry_id") != entry_id]
        return {}


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


async def _open_entries(app, pilot) -> EntriesScreen:
    """초기 화면이 EntriesScreen (CL #51023) — 자체적으로 sections + accounts
    + entries 가 부팅될 때까지만 기다린다."""
    await _wait_for(
        lambda: isinstance(app.screen, EntriesScreen)
        and app.session.section_id == "s1"
        and app.session.id_of("식비") == "x20",
        timeout=3.0,
    )
    await _wait_for(
        lambda: app.screen.last_entry_count >= 1, timeout=2.0,
    )
    return app.screen  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_new_entry_action_pushes_dialog_and_dismiss_creates():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_new_entry()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)
        # 사용자가 폼을 채워 저장한 것과 같은 결과를 직접 dismiss
        draft = EntryDraft(
            entry_date="20260511", money=4500,
            l_account_id="x21", r_account_id="x11",
            item="버스",
        )
        app.screen.dismiss(draft)
        ok = await _wait_for(
            lambda: len(fake.create_calls) == 1, timeout=3.0,
        )
        assert ok
        call = fake.create_calls[0]
        assert call["section_id"] == "s1"
        assert call["money"] == 4500
        assert call["l_account_id"] == "x21"
        assert call["l_account"] == "expenses"  # SessionState 의 type
        assert call["r_account_id"] == "x11"
        assert call["r_account"] == "assets"
        assert call["item"] == "버스"
        # 재로드되어 row count 가 늘어났는지
        await _wait_for(
            lambda: app.screen.query_one("#entries-table", DataTable).row_count == 2,
            timeout=2.0,
        )


@pytest.mark.asyncio
async def test_edit_action_uses_selected_row_and_calls_update():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)
        # 폼은 e1 의 값으로 prefill 되어야. 사용자가 money 만 바꾼 결과:
        draft = EntryDraft(
            entry_date="20260510", money=20000,
            l_account_id="x20", r_account_id="x11",
            item="스타벅스",
            entry_id="e1",
        )
        app.screen.dismiss(draft)
        ok = await _wait_for(
            lambda: len(fake.update_calls) == 1, timeout=3.0,
        )
        assert ok
        call = fake.update_calls[0]
        assert call["entry_id"] == "e1"
        assert call["money"] == 20000


@pytest.mark.asyncio
async def test_delete_action_requires_confirm_yes():
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_delete_entry()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        # No 누르면 삭제 안 됨
        app.screen.dismiss(False)
        await pilot.pause()
        assert fake.delete_calls == []
        # 다시 열어서 Yes
        es.action_delete_entry()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        app.screen.dismiss(True)
        ok = await _wait_for(
            lambda: len(fake.delete_calls) == 1, timeout=3.0,
        )
        assert ok
        assert fake.delete_calls[0]["entry_id"] == "e1"
        # 재로드 후 row 0 건
        await _wait_for(
            lambda: app.screen.query_one("#entries-table", DataTable).row_count == 0,
            timeout=2.0,
        )


@pytest.mark.asyncio
async def test_create_failure_shows_error_status():
    fake = FakeClient()
    fake.create_error = ToolError("USER_INPUT", "잘못된 파라미터")
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_new_entry()
        await pilot.pause()
        draft = EntryDraft(
            entry_date="20260511", money=1000,
            l_account_id="x20", r_account_id="x11",
        )
        app.screen.dismiss(draft)
        ok = await _wait_for(
            lambda: "USER_INPUT" in es.last_status,
            timeout=3.0,
        )
        assert ok


@pytest.mark.asyncio
async def test_update_persists_memo_and_tags_to_local_sqlite():
    """CL #51076+: 거래 수정 후 memo + 해시태그가 로컬 sqlite 에 저장."""
    from whooing_core import db as core_db
    from whooing_tui import data as tui_data

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        assert isinstance(app.screen, EntryEditDialog)
        # 사용자가 memo + tags 를 채워 저장한 결과를 직접 dismiss.
        draft = EntryDraft(
            entry_date="20260510", money=12000,
            l_account_id="x20", r_account_id="x11",
            l_type="expenses", r_type="assets",
            item="스타벅스", memo="회의비 정산",
            tags=["커피", "회의"],
            entry_id="e1",
        )
        app.screen.dismiss(draft)
        ok = await _wait_for(
            lambda: len(fake.update_calls) == 1, timeout=3.0,
        )
        assert ok
        # 후잉 호출의 memo 가 포함됐는지
        assert fake.update_calls[0]["memo"] == "회의비 정산"
        # 로컬 db 에서도 같은 memo + 해시태그 확인
        await _wait_for(
            lambda: tui_data.db_path().exists(), timeout=2.0,
        )
        with tui_data.open_ro() as conn:
            info = core_db.get_annotations_for(conn, ["e1"])
        assert info["e1"]["note"] == "회의비 정산"
        assert sorted(info["e1"]["hashtags"]) == ["커피", "회의"]


@pytest.mark.asyncio
async def test_delete_purges_local_annotation_and_tags():
    """CL #51076+: 거래 삭제 시 로컬 annotation/해시태그도 함께 정리.
    CL #51132+ (A1): 첨부 row + 디스크 파일도 함께 정리 — orphan 방지.
    """
    from whooing_core import attachments as core_attach
    from whooing_core import db as core_db
    from whooing_tui import data as tui_data

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        tui_data.init_shared_schema()
        # annotation + 해시태그 + 첨부 모두 심어둠.
        with tui_data.open_rw() as conn:
            core_db.upsert_annotation(
                conn, entry_id="e1", section_id="s1", note="기존메모",
            )
            core_db.set_hashtags(conn, "e1", ["삭제전"])
        # 첨부 디스크 + db row 모두.
        root = tui_data.attachments_root()
        src = tui_data.db_path().parent / "test_receipt.pdf"
        src.write_bytes(b"PDF FAKE")
        copied, sha, size = core_attach.copy_to_attachments(
            src, attachments_root=root, attach_date="2026-05-10",
        )
        rel = str(copied.relative_to(root))
        with tui_data.open_rw() as conn:
            core_attach.upsert_attachment(
                conn, entry_id="e1", section_id="s1",
                file_path=rel, original_path=str(src),
                original_filename="test_receipt.pdf",
                file_size_bytes=size, file_sha256=sha,
                mime_type="application/pdf", note=None,
            )
        # 거래 삭제.
        es.action_delete_entry()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        app.screen.dismiss(True)
        ok = await _wait_for(
            lambda: len(fake.delete_calls) == 1, timeout=3.0,
        )
        assert ok
        await _wait_for(
            lambda: app.screen.query_one("#entries-table", DataTable).row_count == 0,
            timeout=2.0,
        )
        # annotation + tag + 첨부 row + 디스크 파일 모두 정리.
        with tui_data.open_ro() as conn:
            info = core_db.get_annotations_for(conn, ["e1"])
            attach = core_attach.list_attachments_for(conn, ["e1"])
        assert info == {}
        assert attach == {}
        assert not copied.exists()


@pytest.mark.asyncio
async def test_tags_input_enter_pushes_picker_and_appends():
    """CL #51080+: tags Input 에서 Enter → TagsPickerScreen push, 결과를
    Input value 에 공백 구분으로 append."""
    from textual.widgets import Input

    from whooing_tui.screens.tags_picker import TagsPickerScreen

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        tags_input = dialog.query_one("#f-tags", Input)
        # 사용자가 tags Input 에 focus 한 상태에서 Enter — Submitted 이벤트.
        dialog.post_message(Input.Submitted(tags_input, ""))
        await pilot.pause()
        assert isinstance(app.screen, TagsPickerScreen)
        # 사용자가 "커피" 를 골라 dismiss.
        app.screen.dismiss("커피")
        await pilot.pause()
        # dialog 로 복귀 + tags Input 에 추가됨.
        assert isinstance(app.screen, EntryEditDialog)
        assert "커피" in tags_input.value
        # 다시 Enter, 다른 태그 추가 — 공백 구분.
        dialog.post_message(Input.Submitted(tags_input, tags_input.value))
        await pilot.pause()
        app.screen.dismiss("회의")
        await pilot.pause()
        # CL #51115+: append 시 `#` prefix 로 표시 (저장은 bare).
        assert tags_input.value == "#커피 #회의"


@pytest.mark.asyncio
async def test_delete_with_no_selection_shows_error():
    fake = FakeClient(entries=[])
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # 초기 화면이 EntriesScreen, entries 빈 응답으로 진입
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        es.action_delete_entry()
        await pilot.pause()
        # ConfirmModal 이 뜨면 안 됨
        assert not isinstance(app.screen, ConfirmModal)
        assert "선택된 거래가 없습니다" in es.last_status


# ---- CL #51149+ (H7) tag inline hint -----------------------------------


@pytest.mark.asyncio
async def test_tags_input_typing_shows_hint():
    """타이핑 중 매칭 태그가 hint Static 에 표시 — Enter 안 눌러도."""
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.edit_entry import EntryEditDialog
    from textual.widgets import Input, Static

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        # 기존 태그 시드 — _all_tags_db 에 들어가도록 db.
        from whooing_core import db as core_db
        from whooing_tui import data as tui_data
        with tui_data.open_rw() as conn:
            core_db.set_hashtags(conn, "e_seed_1", ["식비", "식권", "교통비"])
            core_db.upsert_annotation(
                conn, entry_id="e_seed_1", section_id="s1", note=None,
            )
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        tags_input = dialog.query_one("#f-tags", Input)
        # 사용자가 "식" 까지 타이핑.
        tags_input.value = "#식"
        # on_input_changed 가 호출돼 hint 갱신.
        await pilot.pause()
        hint = dialog.query_one("#f-tags-hint", Static)
        rendered = str(hint.render())
        # 매칭 후보 (식비, 식권) 가 hint 에.
        assert "식비" in rendered or "식권" in rendered


@pytest.mark.asyncio
async def test_tags_input_empty_clears_hint():
    """입력란 비면 hint 도 빈 문자열."""
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.edit_entry import EntryEditDialog
    from textual.widgets import Input, Static

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        tags_input = dialog.query_one("#f-tags", Input)
        tags_input.value = ""
        await pilot.pause()
        hint = dialog.query_one("#f-tags-hint", Static)
        assert str(hint.render()).strip() == ""
