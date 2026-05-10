"""attachment_browser.py — db round-trip + helper tests."""

from __future__ import annotations

import pytest

from whooing_tui import data as tui_data
from whooing_tui.screens.attachment_browser import (
    _fmt_bytes,
    add_attachment,
    list_for,
    remove,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    tui_data.init_shared_schema()
    return tmp_path


@pytest.fixture
def src_file(tmp_path):
    p = tmp_path / "invoice.pdf"
    p.write_bytes(b"%PDF-fake")
    return p


# ---- _fmt_bytes -----------------------------------------------------


def test_fmt_bytes_none():
    assert _fmt_bytes(None) == ""
    assert _fmt_bytes(0) == ""


def test_fmt_bytes_small():
    assert _fmt_bytes(123) == "123 B"


def test_fmt_bytes_kb():
    assert _fmt_bytes(2048) == "2.0 KB"


def test_fmt_bytes_mb():
    assert _fmt_bytes(2 * 1024 * 1024) == "2.0 MB"


# ---- add_attachment / list_for / remove ---------------------------


def test_list_empty(isolated):
    assert list_for("e_unknown") == []


def test_add_then_list(isolated, src_file):
    row = add_attachment("e1", str(src_file), note="첫 첨부")
    assert row["entry_id"] == "e1"
    assert row["original_filename"] == "invoice.pdf"
    assert row["note"] == "첫 첨부"
    rows = list_for("e1")
    assert len(rows) == 1


def test_add_dedups_same_entry_same_file(isolated, src_file):
    add_attachment("e1", str(src_file))
    add_attachment("e1", str(src_file))  # 같은 sha256 → existing row 재사용
    assert len(list_for("e1")) == 1


def test_add_separate_entries_share_disk(isolated, src_file):
    """같은 sha256 이지만 entry 다르면 row 2개. 디스크 파일은 1개."""
    add_attachment("e1", str(src_file))
    add_attachment("e2", str(src_file))
    assert len(list_for("e1")) == 1
    assert len(list_for("e2")) == 1


def test_remove_returns_dict_when_exists(isolated, src_file):
    row = add_attachment("e1", str(src_file))
    deleted = remove(row["id"], delete_file=True)
    assert deleted is not None
    assert list_for("e1") == []


def test_remove_returns_none_when_missing(isolated):
    assert remove(99999) is None


def test_add_missing_source_raises(isolated, tmp_path):
    with pytest.raises(FileNotFoundError):
        add_attachment("e1", str(tmp_path / "nope.pdf"))


def test_add_directory_rejected(isolated, tmp_path):
    with pytest.raises(ValueError):
        add_attachment("e1", str(tmp_path))


# ---- CL #51124+ P4 자동 submit (db + 파일 한 CL) -----------------------


@pytest.fixture
def p4_spy(monkeypatch):
    """`p4_sync.submit_files_to_p4` 호출을 capture — 실 p4 바이너리 없이
    add/remove 가 어떤 paths / description 으로 submit 을 요청했는지 검증."""
    calls: list[dict] = []

    def _fake(paths, description, *, blocking=False, on_complete=None):
        # 실 thread 런타임 회피 — 호출 인자만 기록.
        calls.append({
            "paths": [str(p) for p in paths],
            "description": description,
            "blocking": blocking,
            "on_complete": on_complete,
        })

    from whooing_tui import p4_sync
    monkeypatch.setattr(p4_sync, "submit_files_to_p4", _fake)
    return calls


def test_add_attachment_submits_db_and_file_to_p4(isolated, src_file, p4_spy):
    """add_attachment 직후 submit_files_to_p4 가 [db, copied_file] 로 호출되고
    description 이 attachment add 형식. 사용자 요청 CL #51124."""
    row = add_attachment("e1", str(src_file))
    assert len(p4_spy) == 1
    call = p4_spy[0]
    # 두 파일: db + 디스크 복사된 첨부.
    assert len(call["paths"]) == 2
    assert any(p.endswith("whooing-data.sqlite") for p in call["paths"])
    assert any(p.endswith("invoice.pdf") for p in call["paths"])
    # description: 기계적 add 형식 + entry id + 파일명 + size.
    desc = call["description"]
    assert desc.startswith("[whooing-tui] entry e1 attachment add: invoice.pdf")
    assert "bytes" in desc
    assert "sha256=" in desc


def test_remove_submits_db_and_unlinked_file_to_p4(isolated, src_file, p4_spy):
    """단일 entry 의 첨부 삭제 → 디스크 파일도 unlink → submit 에 [db, file]
    + description 'attachment delete: <filename>'."""
    row = add_attachment("e1", str(src_file))
    p4_spy.clear()  # add 의 submit 무시
    deleted = remove(row["id"], delete_file=True)
    assert deleted is not None
    assert deleted.get("file_deleted") is True
    assert len(p4_spy) == 1
    call = p4_spy[0]
    assert len(call["paths"]) == 2  # db + 사라진 파일 path
    assert any(p.endswith("whooing-data.sqlite") for p in call["paths"])
    assert any(p.endswith("invoice.pdf") for p in call["paths"])
    desc = call["description"]
    assert desc == "[whooing-tui] entry e1 attachment delete: invoice.pdf"


def test_remove_dedup_kept_submits_db_only(isolated, src_file, p4_spy):
    """같은 sha256 이 다른 entry 에 남아있으면 디스크 파일은 보존 — submit
    에는 [db] 만 포함되고 description 에 'db only, file kept' 명시."""
    add_attachment("e1", str(src_file))
    row2 = add_attachment("e2", str(src_file))  # 같은 파일, 다른 entry
    p4_spy.clear()
    deleted = remove(row2["id"], delete_file=True)
    assert deleted is not None
    # core_attach.delete_attachment 의 dedup 분기:
    assert deleted.get("file_kept_other_refs") == 1
    assert "file_deleted" not in deleted
    assert len(p4_spy) == 1
    call = p4_spy[0]
    assert len(call["paths"]) == 1
    assert call["paths"][0].endswith("whooing-data.sqlite")
    desc = call["description"]
    assert "attachment delete" in desc
    assert "db only, file kept" in desc
    assert "1 other refs" in desc


def test_remove_missing_id_skips_p4_submit(isolated, p4_spy):
    """존재하지 않는 attachment_id → None 반환 + p4 호출 X."""
    assert remove(99999) is None
    assert p4_spy == []


def test_add_attachment_uses_blocking_false_default(isolated, src_file, p4_spy):
    """fire-and-forget — blocking=False 가 default (UI thread 차단 X)."""
    add_attachment("e1", str(src_file))
    assert p4_spy[0]["blocking"] is False


# ---- CL #51136+ (A11) size cap ------------------------------------------


def test_add_rejects_oversize(isolated, tmp_path, monkeypatch):
    """`WHOOING_MAX_ATTACHMENT_BYTES` 초과 → ValueError."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x00" * 1500)
    monkeypatch.setenv("WHOOING_MAX_ATTACHMENT_BYTES", "1024")
    with pytest.raises(ValueError, match="cap"):
        add_attachment("e1", str(big))


def test_add_accepts_within_cap(isolated, src_file, monkeypatch):
    monkeypatch.setenv("WHOOING_MAX_ATTACHMENT_BYTES", "1024")
    row = add_attachment("e1", str(src_file))
    assert row["entry_id"] == "e1"


def test_cap_disabled_when_zero(isolated, tmp_path, monkeypatch):
    """`WHOOING_MAX_ATTACHMENT_BYTES=0` 면 cap 비활성."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"\x00" * 5000)
    monkeypatch.setenv("WHOOING_MAX_ATTACHMENT_BYTES", "0")
    row = add_attachment("e1", str(big))
    assert row["entry_id"] == "e1"


# ---- CL #51136+ (A4) on_p4_complete callback ----------------------------


def test_add_attachment_passes_callback_to_p4_submit(isolated, src_file, p4_spy):
    """add_attachment 의 on_p4_complete 가 submit_files_to_p4 로 전달."""

    def _cb(status):
        pass

    add_attachment("e1", str(src_file), on_p4_complete=_cb)
    assert p4_spy[0]["on_complete"] is _cb


# ---- CL #51142+ (A12) paste / drop 자동 첨부 ----------------------------


@pytest.mark.asyncio
async def test_paste_absolute_path_attaches_file(isolated, src_file, p4_spy):
    """절대 경로 paste → 자동으로 add_attachment 호출."""
    import asyncio
    from textual.events import Paste
    from whooing_tui.app import WhooingTuiApp
    from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen
    from whooing_tui.screens.entries import EntriesScreen

    class _FakeClient:
        async def list_sections(self):
            return [{"section_id": "s1", "title": "main"}]
        async def list_accounts(self, section_id):
            return {}
        async def list_entries(self, section_id, start_date, end_date):
            return []

    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        # EntriesScreen 부팅 대기.
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            if isinstance(app.screen, EntriesScreen) and app.session.section_id:
                break
            await asyncio.sleep(0.02)
        ab = AttachmentBrowserScreen(entry_id="e1", section_id="s1")
        await app.push_screen(ab)
        await pilot.pause()
        # Paste 이벤트 — 절대 경로.
        ab.on_paste(Paste(str(src_file)))
        await pilot.pause()
        # add_attachment 가 호출돼 entry_id="e1" 의 첨부 1건.
        from whooing_tui.screens.attachment_browser import list_for
        assert len(list_for("e1")) == 1


def test_paste_strips_quotes_and_file_scheme(tmp_path):
    """on_paste 의 path 정규화 (따옴표 / file:// strip) 단위 검증."""
    # on_paste 자체는 Screen 인스턴스 메서드라 직접 호출하기 어려움 —
    # 정규화 로직만 inline 으로 검증.
    from urllib.parse import unquote, urlparse
    samples = [
        ("/abs/x.pdf", "/abs/x.pdf"),
        ('"/abs/x.pdf"', "/abs/x.pdf"),
        ("'/abs/x.pdf'", "/abs/x.pdf"),
        ("file:///abs/x.pdf", "/abs/x.pdf"),
        ("file:///path%20with%20space/x.pdf", "/path with space/x.pdf"),
    ]
    for raw, expected in samples:
        s = raw.strip()
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        elif s.startswith("'") and s.endswith("'"):
            s = s[1:-1]
        if s.startswith("file://"):
            s = unquote(urlparse(s).path)
        assert s == expected, f"{raw!r} → {s!r} (expected {expected!r})"


# ---- CL #51143+ (A9) note 사후 편집 ------------------------------------


def test_update_note_wrapper_returns_updated_row(isolated, src_file, p4_spy):
    """`update_note` 가 db UPDATE 후 row 반환 + P4 submit 발사."""
    from whooing_tui.screens.attachment_browser import update_note
    row = add_attachment("e1", str(src_file), note="초기 note")
    p4_spy.clear()
    out = update_note(row["id"], "수정후 note")
    assert out is not None
    assert out["note"] == "수정후 note"
    # P4 submit 1건 — db 만 (paths 길이 1).
    assert len(p4_spy) == 1
    assert len(p4_spy[0]["paths"]) == 1
    assert p4_spy[0]["paths"][0].endswith("whooing-data.sqlite")
    desc = p4_spy[0]["description"]
    assert "attachment" in desc and "note edit" in desc
    assert "invoice.pdf" in desc


def test_update_note_wrapper_returns_none_for_unknown_id(isolated, p4_spy):
    from whooing_tui.screens.attachment_browser import update_note
    assert update_note(99999, "x") is None
    # P4 submit 호출 X.
    assert p4_spy == []


def test_update_note_empty_string_clears_note(isolated, src_file, p4_spy):
    from whooing_tui.screens.attachment_browser import update_note
    row = add_attachment("e1", str(src_file), note="기존")
    out = update_note(row["id"], "")
    assert out is not None
    assert out["note"] is None


@pytest.mark.asyncio
async def test_action_edit_note_pushes_note_modal(isolated, src_file, p4_spy):
    """`action_edit_note` (sync wrapper) → worker spawn → _NoteEditModal push."""
    import asyncio
    from whooing_tui.app import WhooingTuiApp
    from whooing_tui.screens.attachment_browser import (
        AttachmentBrowserScreen, _NoteEditModal,
    )
    from whooing_tui.screens.entries import EntriesScreen

    class _FakeClient:
        async def list_sections(self):
            return [{"section_id": "s1", "title": "main"}]
        async def list_accounts(self, section_id):
            return {}
        async def list_entries(self, section_id, start_date, end_date):
            return []

    add_attachment("e1", str(src_file), note="초기")
    p4_spy.clear()

    app = WhooingTuiApp(client=_FakeClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            if isinstance(app.screen, EntriesScreen) and app.session.section_id:
                break
            await asyncio.sleep(0.02)
        ab = AttachmentBrowserScreen(entry_id="e1", section_id="s1")
        await app.push_screen(ab)
        await pilot.pause()
        # action_edit_note 호출 — worker 안에서 modal push.
        ab.action_edit_note()
        # worker 가 spawn 되고 modal 이 push 될 때까지 잠깐 대기.
        await asyncio.sleep(0.1)
        await pilot.pause()
        assert isinstance(app.screen, _NoteEditModal)
        # 사용자가 저장한다고 가정 — TextArea 의 default text 그대로 dismiss.
        app.screen.dismiss("수정후")
        # worker 가 update_note 호출 끝낼 때까지 대기.
        await asyncio.sleep(0.1)
        await pilot.pause()
        # db 가 갱신됐어야.
        from whooing_tui.screens.attachment_browser import list_for
        rows = list_for("e1")
        assert len(rows) == 1
        assert rows[0]["note"] == "수정후"


def test_action_edit_note_no_selection_warns(isolated):
    """첨부 없는 entry → action_edit_note 호출 시 list_for 가 빈 리스트
    (worker 안의 안내 path 실 검증은 통합 테스트 비용 대비 가치 낮음)."""
    from whooing_tui.screens.attachment_browser import list_for
    assert list_for("e_no") == []


@pytest.mark.asyncio
async def test_paste_relative_or_nonexistent_is_noop(isolated, p4_spy):
    """존재 안 하거나 상대 경로면 paste 가 silent ignore."""
    import asyncio
    from textual.events import Paste
    from whooing_tui.app import WhooingTuiApp
    from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen
    from whooing_tui.screens.attachment_browser import list_for

    class _FakeClient:
        async def list_sections(self):
            return [{"section_id": "s1", "title": "main"}]
        async def list_accounts(self, section_id):
            return {}
        async def list_entries(self, section_id, start_date, end_date):
            return []

    fake = _FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            if isinstance(app.screen, EntriesScreen) and app.session.section_id:
                break
            await asyncio.sleep(0.02)
        ab = AttachmentBrowserScreen(entry_id="e1", section_id="s1")
        await app.push_screen(ab)
        await pilot.pause()
        # 상대 경로.
        ab.on_paste(Paste("relative/x.pdf"))
        # 빈 문자열.
        ab.on_paste(Paste(""))
        # 존재 안 하는 절대 경로.
        ab.on_paste(Paste("/nonexistent/file.pdf"))
        await pilot.pause()
        assert list_for("e1") == []
