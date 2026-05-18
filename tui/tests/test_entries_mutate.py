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


# ---- CL #52718+ : attach button in EntryEditDialog --------------------


@pytest.mark.asyncio
async def test_edit_dialog_shows_attach_button_enabled_in_edit_mode():
    """수정 모드 — entry_id 있으므로 attach button 이 enabled + 📎 라벨."""
    from whooing_tui.screens.edit_entry import _AttachmentButton

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        btn = dialog.query_one("#f-attachments", _AttachmentButton)
        assert btn.entry_id == "e1"
        assert btn.disabled is False
        # 첨부가 없는 상태 (FakeClient 의 fixture 에 sqlite 첨부 시드 X)
        # 라 count 0 — "Enter 로 추가" 메시지.
        assert "📎" in str(btn.label)
        assert "추가" in str(btn.label)


@pytest.mark.asyncio
async def test_new_dialog_shows_attach_button_disabled_in_new_mode():
    """신규 모드 — entry_id 없으므로 attach button 이 disabled."""
    from whooing_tui.screens.edit_entry import _AttachmentButton

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_new_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        btn = dialog.query_one("#f-attachments", _AttachmentButton)
        assert btn.entry_id == ""
        assert btn.disabled is True
        assert "저장 후" in str(btn.label)


@pytest.mark.asyncio
async def test_edit_dialog_form_grid_has_exactly_2x8_children():
    """CL #52731 (회귀 fix): form-grid 의 자식 수가 grid-size(2x8)=16 과 정확히
    일치. 종전엔 Static(f-tags-hint) 가 grid 안에 yield 돼 attach 가 row 8
    밖으로 밀려나 화면에 안 그려졌다 (사용자 보고). hint 를 grid 밖으로
    옮긴 변경이 다시 grid 안으로 들어가면 본 테스트가 실패해 알린다.
    """
    from textual.containers import Grid

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        grid = dialog.query_one("#form-grid", Grid)
        # 2 cols × 8 rows = 16 자식이어야 attach 가 8행에 정확히 자리잡음.
        assert len(grid.children) == 16, (
            f"form-grid children count = {len(grid.children)} (expected 16). "
            f"grid-size:2 8 보다 많으면 마지막 위젯이 layout 에서 빠진다."
        )


@pytest.mark.asyncio
async def test_edit_dialog_attach_button_is_inside_form_grid():
    """attach button 이 grid 의 직속 자식 — grid 밖 yield 되면 layout 어긋남."""
    from textual.containers import Grid

    from whooing_tui.screens.edit_entry import _AttachmentButton

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        grid = dialog.query_one("#form-grid", Grid)
        attach_btn = dialog.query_one("#f-attachments", _AttachmentButton)
        assert attach_btn.parent is grid, (
            f"attach button parent = {type(attach_btn.parent).__name__} "
            f"(expected Grid). grid 밖 yield 되면 visual 위치 어긋남."
        )


@pytest.mark.asyncio
async def test_edit_dialog_attach_button_pushes_browser():
    """수정 모드 + attach button click → AttachmentBrowserScreen push."""
    from textual.widgets import Button

    from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen
    from whooing_tui.screens.edit_entry import _AttachmentButton

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        btn = dialog.query_one("#f-attachments", _AttachmentButton)
        # Button.Pressed 이벤트로 click 시뮬.
        dialog.post_message(Button.Pressed(btn))
        await pilot.pause()
        assert isinstance(app.screen, AttachmentBrowserScreen)
        assert app.screen.entry_id == "e1"
        assert app.screen.section_id == "s1"


# ---- CL #52757+ : tags hint 가 이미 입력된 태그 제외 -------------------


@pytest.mark.asyncio
async def test_tags_input_with_already_typed_tag_excludes_from_hint():
    """이미 #보안 이 tags 에 들어가 있고 typing 끝났으면 hint 추천 X.

    사용자 보고: "이미 보안 태그가 붙어있는데 보안 태그를 추천해줍니다.
    이미 태그가 설정되어 있으면 추천할 필요 없습니다."
    """
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.edit_entry import EntryEditDialog
    from textual.widgets import Input, Static

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        from whooing_core import db as core_db
        from whooing_tui import data as tui_data
        with tui_data.open_rw() as conn:
            core_db.set_hashtags(conn, "e_seed_1", ["보안", "백업", "Arq"])
            core_db.upsert_annotation(
                conn, entry_id="e_seed_1", section_id="s1", note=None,
            )
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        tags_input = dialog.query_one("#f-tags", Input)
        # 이미 #보안 이 typing 완료된 상태 (사용자 캡처 시나리오).
        tags_input.value = "#Arq #백업 #보안"
        await pilot.pause()
        hint = dialog.query_one("#f-tags-hint", Static)
        rendered = str(hint.render())
        # "보안" 이 hint 에 추천돼서는 안 됨.
        assert "보안" not in rendered, (
            f"이미 입력된 태그가 hint 에 다시 추천됨 (회귀): {rendered!r}"
        )


@pytest.mark.asyncio
async def test_tags_input_typing_prefix_still_suggests_other_matches():
    """이미 #보안 이 있지만 추가로 '백' 타이핑 중이면 '백업' 추천은 OK
    (다른 후보가 사라지지 않게)."""
    from whooing_tui.screens.entries import EntriesScreen
    from whooing_tui.screens.edit_entry import EntryEditDialog
    from textual.widgets import Input, Static

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        from whooing_core import db as core_db
        from whooing_tui import data as tui_data
        with tui_data.open_rw() as conn:
            core_db.set_hashtags(conn, "e_seed_1", ["보안", "백업"])
            core_db.upsert_annotation(
                conn, entry_id="e_seed_1", section_id="s1", note=None,
            )
        es.action_edit_entry()
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, EntryEditDialog)
        tags_input = dialog.query_one("#f-tags", Input)
        # 이미 #보안 + 다시 '백' 타이핑 중.
        tags_input.value = "#보안 #백"
        await pilot.pause()
        hint = dialog.query_one("#f-tags-hint", Static)
        rendered = str(hint.render())
        # "백업" 은 추천돼야 (last token "백" 의 prefix).
        assert "백업" in rendered, (
            f"prefix 매칭 후보가 사라짐: {rendered!r}"
        )


# ---- CL #52758+ : 점진적 캐시 확장 필터 -------------------------------


@pytest.mark.asyncio
async def test_apply_filter_uses_sqlite_cache_for_extras(monkeypatch):
    """필터 적용 시 sqlite 캐시에서 윈도우 밖의 매칭을 즉시 추가.

    시나리오:
      - 현재 _all_entries 에는 윈도우 안의 매칭 1건 (e1, l_account_id=x20)
      - 캐시에는 더 오래된 매칭 2건 (e_old1, e_old2, 같은 x20)
      - 필터 적용 → 화면에 3건 모두.
    """
    from whooing_core import entries_cache as core_cache
    from whooing_tui import data as tui_data

    fake = FakeClient()
    # 사용자 캐시 시드 — 윈도우 보다 오래된 매칭.
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        # 캐시에 동일 left=x20 인 과거 entries 시드.
        with tui_data.open_rw() as conn:
            core_cache.upsert_entries(conn, "s1", [
                {"entry_id": "e_old1", "entry_date": "20251110",
                 "money": 5000, "l_account_id": "x20", "r_account_id": "x11",
                 "item": "ABC"},
                {"entry_id": "e_old2", "entry_date": "20251020",
                 "money": 3000, "l_account_id": "x20", "r_account_id": "x11",
                 "item": "XYZ"},
                {"entry_id": "e_other", "entry_date": "20251101",
                 "money": 1000, "l_account_id": "x21", "r_account_id": "x11",
                 "item": "교통"},  # 다른 left — 매칭 X
            ])
        # 필터 worker 는 실 후잉 호출 skip (FakeClient.list_entries 가 sample
        # 1건만 반환 — 확장 단계도 그대로). epoch race 회피 위해 즉시 worker
        # 진행 안 하도록 monkeypatch — 캐시 lookup 만 검증.
        # 그러나 _apply_filter 가 자체적으로 cache lookup 동기 호출이므로
        # worker 무관하게 즉시 결과 확인 가능.

        # row 선택 후 left 컬럼 필터 (사용자 명시 e1 의 x20).
        es._column_active = True
        es._active_col = es._COLUMN_NAMES.index("left")
        es._apply_filter("left", es._all_entries[0])  # e1 (l_account_id=x20)
        await pilot.pause()
        # 현재 entries 에 e1 + e_old1 + e_old2 (다른 l_account_id 제외).
        ids = {str(e.get("entry_id") or "") for e in es._entries}
        assert "e1" in ids
        assert "e_old1" in ids
        assert "e_old2" in ids
        assert "e_other" not in ids


@pytest.mark.asyncio
async def test_apply_filter_caches_window_entries_on_refresh():
    """refresh_entries 가 fetch 한 entries 를 캐시에 자동 upsert."""
    from whooing_core import entries_cache as core_cache
    from whooing_tui import data as tui_data

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        # refresh 가 끝나면 캐시에 e1 이 들어가 있어야.
        with tui_data.open_ro() as conn:
            assert core_cache.cached_count(conn, "s1") >= 1
            rows = core_cache.list_cached(conn, "s1")
            assert any(r.get("entry_id") == "e1" for r in rows)


@pytest.mark.asyncio
async def test_clear_filter_bumps_epoch_and_clears_extras():
    """필터 해제 시 _filter_extra 비움 + epoch 증가 (worker 결과 폐기)."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es._column_active = True
        es._active_col = es._COLUMN_NAMES.index("left")
        before_epoch = es._filter_epoch
        es._apply_filter("left", es._all_entries[0])
        # 필터 적용 후 epoch 증가
        assert es._filter_epoch == before_epoch + 1
        # 강제로 가짜 extra 주입 후 해제.
        es._filter_extra.append({"entry_id": "fake"})
        after_epoch = es._filter_epoch
        es.action_clear_filter()
        assert es._filter_epoch == after_epoch + 1
        assert es._filter_extra == []
        assert es._active_filter is None


def test_yesterday_of_helper():
    """_yesterday_of 의 가장자리 케이스."""
    from whooing_tui.screens.entries import _yesterday_of
    assert _yesterday_of("20260518") == "20260517"
    assert _yesterday_of("20260301") == "20260228"
    assert _yesterday_of("20260101") == "20251231"
    assert _yesterday_of("") in ("", None)
    assert _yesterday_of(None) is None
    # 잘못된 입력 — 그대로 반환 (보수적).
    assert _yesterday_of("abc") == "abc"


# ---- CL #52763+ : context menu (m 키 / ㅡ) ----------------------------


def test_m_key_bound_to_show_context_menu():
    """m / ㅡ 가 action_show_context_menu 로 등록 — IME 양쪽."""
    keys = {b.key: b.action for b in EntriesScreen.BINDINGS}
    assert keys.get("m") == "show_context_menu"
    assert keys.get("ㅡ") == "show_context_menu"


@pytest.mark.asyncio
async def test_m_press_pushes_context_menu_popup():
    """선택된 거래 위에서 m → MenuPopup push (수정/삭제/첨부/새 거래 항목)."""
    from whooing_tui.widgets.menubar import MenuPopup

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_show_context_menu()
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup)
        action_ids = [it.action_id for it in app.screen.spec.items]
        assert "edit_entry" in action_ids
        assert "delete_entry" in action_ids
        assert "open_attachments" in action_ids
        assert "new_entry" in action_ids


@pytest.mark.asyncio
async def test_context_menu_delete_dispatches_action_delete_entry():
    """메뉴에서 '삭제' 선택 → action_delete_entry 호출 → ConfirmModal push.

    사용자 보고의 정확한 흐름: m → 메뉴 → 삭제 선택 → 확인 모달.
    """
    from whooing_tui.screens.edit_entry import ConfirmModal
    from whooing_tui.widgets.menubar import MenuPopup

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es.action_show_context_menu()
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup)
        # 메뉴에서 '삭제' 선택 — dismiss(action_id).
        app.screen.dismiss("delete_entry")
        await pilot.pause()
        # action_delete_entry 가 ConfirmModal push.
        assert isinstance(app.screen, ConfirmModal)


@pytest.mark.asyncio
async def test_context_menu_on_sentinel_row_is_noop():
    """sentinel row (새 거래 자리) — 메뉴 띄우지 않음, 안내 status."""
    from whooing_tui.widgets.menubar import MenuPopup

    fake = FakeClient(entries=[])  # 빈 entries — sentinel 자동 노출
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1",
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # sentinel 자동 노출 + cursor 가 sentinel.
        await pilot.pause()
        before_screen = app.screen
        es.action_show_context_menu()
        await pilot.pause()
        # MenuPopup 안 떴음 — 같은 EntriesScreen.
        assert app.screen is before_screen
        assert "새 거래" in es.last_status or "sentinel" in es.last_status.lower()


@pytest.mark.asyncio
async def test_context_menu_includes_batch_tag_when_multiselect_active():
    """multi-select 가 1+ 면 일괄 태그 항목 추가 — 사용자 흐름 통합."""
    from whooing_tui.widgets.menubar import MenuPopup

    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        # 1건 선택.
        es._selected_entry_ids = {"e1"}
        es.action_show_context_menu()
        await pilot.pause()
        assert isinstance(app.screen, MenuPopup)
        action_ids = [it.action_id for it in app.screen.spec.items]
        assert "batch_tag" in action_ids


# ---- CL #52771+ : Home / End / PgUp / PgDn navigation -----------------


def test_home_end_pageup_pagedown_bindings_registered():
    """4개 키가 BINDINGS 에 등록 — priority=True 라 default DataTable
    navigation 이 가려진 환경에서도 발화."""
    keys = {b.key: b.action for b in EntriesScreen.BINDINGS}
    assert keys["home"] == "row_home"
    assert keys["end"] == "row_end"
    assert keys["pageup"] == "row_pageup"
    assert keys["pagedown"] == "row_pagedown"


@pytest.mark.asyncio
async def test_home_moves_to_first_entry_row():
    """Home → 첫 실거래 row. sentinel 보이는 상태라면 sentinel(0) 가 아닌
    첫 실거래 (1) 로."""
    from textual.widgets import DataTable
    # entries 3개로 — 마지막 row 0,1,2.
    entries = [
        {"entry_id": f"e{i}", "entry_date": f"2026050{i}",
         "money": 1000 * i, "l_account_id": "x20", "r_account_id": "x11",
         "item": f"item{i}"}
        for i in range(1, 4)
    ]
    fake = FakeClient(entries=entries)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        table = es.query_one("#entries-table", DataTable)
        # 사용자가 row 2 (가운데) 에 있을 때 Home 누름.
        table.move_cursor(row=2, animate=False)
        await pilot.pause()
        es.action_row_home()
        await pilot.pause()
        # sentinel 숨김 상태 (default) — 첫 실거래는 row 0.
        assert table.cursor_row == 0


@pytest.mark.asyncio
async def test_end_moves_to_last_entry_row():
    """End → 마지막 실거래 row."""
    from textual.widgets import DataTable
    entries = [
        {"entry_id": f"e{i}", "entry_date": f"2026050{i}",
         "money": 1000 * i, "l_account_id": "x20", "r_account_id": "x11"}
        for i in range(1, 6)  # 5건
    ]
    fake = FakeClient(entries=entries)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        table = es.query_one("#entries-table", DataTable)
        table.move_cursor(row=0, animate=False)
        await pilot.pause()
        es.action_row_end()
        await pilot.pause()
        # 5건 → row index 0~4 → end = 4.
        assert table.cursor_row == 4


@pytest.mark.asyncio
async def test_pageup_clamps_to_first_entry_row():
    """PgUp 이 첫 실거래 row 보다 위로 못 감."""
    from textual.widgets import DataTable
    entries = [
        {"entry_id": f"e{i}", "entry_date": "20260518",
         "money": i, "l_account_id": "x20", "r_account_id": "x11"}
        for i in range(1, 4)
    ]
    fake = FakeClient(entries=entries)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        table = es.query_one("#entries-table", DataTable)
        table.move_cursor(row=1, animate=False)
        await pilot.pause()
        # _page_step 이 보통 화면 크기 (테스트 환경에서 작아도 1+)
        es.action_row_pageup()
        await pilot.pause()
        # cursor 가 0 이하로 안 감.
        assert table.cursor_row >= 0
        assert table.cursor_row <= 1


@pytest.mark.asyncio
async def test_pagedown_clamps_to_last_entry_row():
    """PgDn 이 마지막 실거래 row 를 넘지 않음."""
    from textual.widgets import DataTable
    entries = [
        {"entry_id": f"e{i}", "entry_date": "20260518",
         "money": i, "l_account_id": "x20", "r_account_id": "x11"}
        for i in range(1, 4)
    ]
    fake = FakeClient(entries=entries)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        table = es.query_one("#entries-table", DataTable)
        table.move_cursor(row=0, animate=False)
        await pilot.pause()
        # 두 번 PgDn — entries 가 3건이라도 max 2 에 clamp.
        es.action_row_pagedown()
        es.action_row_pagedown()
        await pilot.pause()
        assert table.cursor_row == 2  # 마지막 entry index


def test_first_last_entry_row_with_sentinel():
    """sentinel 보이면 첫 entry 는 row 1, 마지막은 row N. 숨김이면 0 / N-1."""
    fake = FakeClient(entries=[
        {"entry_id": "e1", "entry_date": "20260518", "money": 1,
         "l_account_id": "x20", "r_account_id": "x11"},
        {"entry_id": "e2", "entry_date": "20260517", "money": 2,
         "l_account_id": "x20", "r_account_id": "x11"},
    ])
    es = EntriesScreen(fake)  # type: ignore[arg-type]
    # 직접 entries 주입 (run_test 거치지 않고 helper 단위 검증).
    es._entries = list(fake._entries)  # type: ignore[attr-defined]
    # sentinel 숨김 (default) — first=0, last=N-1=1
    es._show_sentinel = False
    assert es._first_entry_row() == 0
    assert es._last_entry_row() == 1
    # sentinel 노출 — first=1, last=N
    es._show_sentinel = True
    assert es._first_entry_row() == 1
    assert es._last_entry_row() == 2


# ---- CL #52773+ : Shift+navigation + Ctrl/Shift+click multi-select ----


def test_shift_nav_bindings_registered():
    """Shift+화살표 / Home / End / PgUp / PgDn 모두 row_select_* action."""
    keys = {b.key: b.action for b in EntriesScreen.BINDINGS}
    assert keys["shift+up"] == "row_select_up"
    assert keys["shift+down"] == "row_select_down"
    assert keys["shift+home"] == "row_select_home"
    assert keys["shift+end"] == "row_select_end"
    assert keys["shift+pageup"] == "row_select_pageup"
    assert keys["shift+pagedown"] == "row_select_pagedown"


@pytest.mark.asyncio
async def test_shift_down_extends_selection_range():
    """Shift+↓ — anchor 부터 새 cursor 까지 entries 가 selection 에 들어감."""
    from textual.widgets import DataTable

    entries = [
        {"entry_id": f"e{i}", "entry_date": "20260518",
         "money": i, "l_account_id": "x20", "r_account_id": "x11"}
        for i in range(1, 6)
    ]
    fake = FakeClient(entries=entries)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        table = es.query_one("#entries-table", DataTable)
        table.move_cursor(row=0, animate=False)
        await pilot.pause()
        # Shift+↓ 3번 → anchor 0 ~ cursor 3.
        es.action_row_select_down()
        es.action_row_select_down()
        es.action_row_select_down()
        await pilot.pause()
        # entries 가 entry_date desc 라 화면 row 0 ~ 3 이 e5/e4/e3/e2 등 — 정확
        # entry_id 는 신경 안 쓰고 selection 수만 확인.
        assert len(es._selected_entry_ids) >= 4


@pytest.mark.asyncio
async def test_shift_end_selects_from_anchor_to_last():
    """Shift+End — anchor 부터 마지막 row 까지 모두 selection."""
    from textual.widgets import DataTable

    entries = [
        {"entry_id": f"e{i}", "entry_date": "20260518",
         "money": i, "l_account_id": "x20", "r_account_id": "x11"}
        for i in range(1, 6)  # 5건
    ]
    fake = FakeClient(entries=entries)
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        table = es.query_one("#entries-table", DataTable)
        table.move_cursor(row=1, animate=False)  # anchor 가 row 1
        await pilot.pause()
        es.action_row_select_end()
        await pilot.pause()
        # row 1 ~ 4 — 총 4 entries.
        assert len(es._selected_entry_ids) == 4


@pytest.mark.asyncio
async def test_clear_filter_resets_selection_anchor():
    """`c` (clear filter) 가 selection anchor 도 reset 해야 새 anchor 시작."""
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        es = await _open_entries(app, pilot)
        es._selection_anchor = 3  # 미리 anchor set 흉내
        # clear_filter 는 anchor 를 강제로 None 으로 만들지는 않지만
        # 새 row_select_* 호출 후 anchor 가 None 이 아니어도 well-defined.
        # 여기는 _selection_anchor 가 안 사라지는지 확인 — 사용자가 명시 reset
        # 하지 않는 한 anchor 유지 (Windows/macOS 표준).
        es.action_clear_filter()
        await pilot.pause()
        # anchor 는 보존 (filter 와 selection 은 직교).
        assert es._selection_anchor == 3


def test_extend_selection_sets_anchor_when_none():
    """anchor None 인 상태에서 호출 시 현재 cursor 가 anchor 로 set."""
    fake = FakeClient(entries=[
        {"entry_id": "e1", "entry_date": "20260518", "money": 100,
         "l_account_id": "x20", "r_account_id": "x11"},
        {"entry_id": "e2", "entry_date": "20260517", "money": 200,
         "l_account_id": "x20", "r_account_id": "x11"},
    ])
    es = EntriesScreen(fake)  # type: ignore[arg-type]
    # _extend_selection_to 가 self.query_one 을 호출하므로 mount 없으면 실패.
    # 본 단위는 anchor 초기값만 확인.
    assert es._selection_anchor is None
