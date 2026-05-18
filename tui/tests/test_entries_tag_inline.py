"""거래 목록의 item 컬럼에 해시태그 인라인 + 태그 단위 column 네비 + 태그
필터 (CL #51102+).

항목:
- _format_cell 의 item 컬럼이 해시태그를 ` #식비 #저녁` 형태로 인라인.
- 다수 태그 (>2) 면 앞 2 + `#…(N)` 축약.
- item 이 비어있어도 태그만 표시.
- ←/→ 가 item 위에서 한 번 더 → 태그 모드 진입, _tag_index += 1.
- 마지막 태그 + → 면 태그 모드 종료 → memo.
- ↑/↓ 로 row 가 바뀌면 태그 모드 자동 종료.
- 태그 선택 상태 marker 는 cyan (`black on cyan`) 으로 일반 노란 marker 와
  구분.
- 태그 위 Enter → 그 태그로 entries 필터, status bar 안내 + r/c/Esc 로 해제.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import DataTable

from whooing_core import db as core_db
from whooing_tui import data as tui_data
from whooing_tui.app import WhooingTuiApp


class FakeClient:
    """간단 FakeClient — entries fetch 만 필요. mutation 없음."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "assets": [{"account_id": "x11", "title": "현금"}],
            "expenses": [
                {"account_id": "x20", "title": "식비"},
                {"account_id": "x21", "title": "교통비"},
            ],
        }
        self._entries = entries

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def create_entry(self, **kw):
        return {"entry_id": "new", **kw}

    async def update_entry(self, **kw):
        return {**kw}

    async def delete_entry(self, **kw):
        return {}


def _seed_local_tags(entry_tags: dict[str, list[str]]) -> None:
    """sqlite 에 entry_id → tag list 사전 삽입 (test_entries_mutate.py 와
    같은 패턴)."""
    tui_data.init_shared_schema()
    with tui_data.open_rw() as conn:
        for eid, tags in entry_tags.items():
            core_db.upsert_annotation(
                conn, entry_id=eid, section_id="s1", note=None,
            )
            core_db.set_hashtags(conn, eid, list(tags))


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


def _sample_entries() -> list[dict[str, Any]]:
    return [
        {"entry_id": "e1", "entry_date": "20260510",
         "money": 12000, "l_account_id": "x20", "r_account_id": "x11",
         "item": "스타벅스"},
        {"entry_id": "e2", "entry_date": "20260509",
         "money": 5500, "l_account_id": "x21", "r_account_id": "x11",
         "item": "지하철"},
        {"entry_id": "e3", "entry_date": "20260508",
         "money": 8000, "l_account_id": "x20", "r_account_id": "x11",
         "item": ""},  # item 비어있음 — 태그만 표시되어야
    ]


# ---- _format_cell 인라인 태그 -----------------------------------------


@pytest.mark.asyncio
async def test_item_cell_inlines_tags_after_text():
    _seed_local_tags({"e1": ["식비", "커피"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen.last_entry_count == 3
            and app.screen._entry_tags.get("e1"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        # row 0 = e1 ("스타벅스"). item 컬럼 (index 4) 의 셀 값.
        item_cell = str(table.get_cell_at((0, 4)))
        assert "스타벅스" in item_cell
        assert "#식비" in item_cell
        assert "#커피" in item_cell


@pytest.mark.asyncio
async def test_item_cell_truncates_many_tags(monkeypatch):
    """CL #52777+: default cap 은 0 (무제한) — 사용자 요청 "모두 보여주세요".
    이 테스트는 cap > 0 일 때만 `#…(N)` 축약이 동작함을 검증 — 환경변수
    `WHOOING_ITEM_TAG_INLINE_LIMIT=2` 명시 시만 잘림.
    """
    monkeypatch.setenv("WHOOING_ITEM_TAG_INLINE_LIMIT", "2")
    _seed_local_tags({"e1": ["식비", "커피", "외식", "회의", "저녁"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and len(app.screen._entry_tags.get("e1", [])) == 5,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        item_cell = str(table.get_cell_at((0, 4)))
        # SQLite 의 entry_hashtags 는 (entry_id, tag) PK 라 fetch 순서가
        # 한글 가나다 순. [식비, 외식, 저녁, 커피, 회의] 중 앞 2 개만 인라인.
        assert "#식비" in item_cell
        assert "#외식" in item_cell
        # 가나다 순으로 3 번째 이후 (저녁 / 커피 / 회의) 는 축약에 들어감.
        assert "#저녁" not in item_cell
        assert "#…(3)" in item_cell


@pytest.mark.asyncio
async def test_item_cell_shows_all_tags_by_default():
    """CL #52777+: default (env 미설정) 면 모든 태그 표시 — 사용자 요청."""
    _seed_local_tags({"e1": ["식비", "커피", "외식", "회의", "저녁"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and len(app.screen._entry_tags.get("e1", [])) == 5,
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        item_cell = str(table.get_cell_at((0, 4)))
        # 모든 5 태그 모두 보여야 — 축약 토큰 없음.
        for t in ("식비", "외식", "저녁", "커피", "회의"):
            assert f"#{t}" in item_cell, (
                f"#{t} 누락 — 기본값이 무제한이 아닌 듯"
            )
        assert "#…" not in item_cell


@pytest.mark.asyncio
async def test_item_cell_shows_only_tags_when_item_empty():
    _seed_local_tags({"e3": ["월세"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen._entry_tags.get("e3"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        table = es.query_one("#entries-table", DataTable)
        # e3 는 sort 후 row 2 (entry_date desc — e1 → e2 → e3).
        item_cell = str(table.get_cell_at((2, 4)))
        # item 빈 문자열 → 태그만 (앞에 공백 없이)
        assert item_cell.strip() == "#월세"


# ---- 태그 단위 column 네비 ---------------------------------------------


@pytest.mark.asyncio
async def test_right_arrow_enters_tag_mode_after_item():
    """item col 활성화 후 → 한 번 더 → tag mode 진입 (`_tag_index = 0`)."""
    _seed_local_tags({"e1": ["식비", "커피"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen._entry_tags.get("e1"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # 첫 → = 활성화 (col 0). 다음 →들로 col 4 (item) 까지.
        for _ in range(5):
            es.action_next_column()
            await pilot.pause()
        assert es._active_col == es._item_col_index()
        assert es._tag_index is None
        # 한 번 더 → → 태그 모드 진입.
        es.action_next_column()
        await pilot.pause()
        assert es._tag_index == 0
        # 다시 → → tag 1.
        es.action_next_column()
        await pilot.pause()
        assert es._tag_index == 1
        # 다시 → → tag 끝, memo 로 진입 (태그 모드 종료).
        es.action_next_column()
        await pilot.pause()
        assert es._tag_index is None
        assert es._active_col == es._memo_col_index()


@pytest.mark.asyncio
async def test_left_arrow_from_memo_to_last_tag():
    """memo 위 ← → 그 row 의 마지막 태그 (있으면) 에서 시작."""
    _seed_local_tags({"e1": ["식비", "커피", "외식"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen._entry_tags.get("e1"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # → 를 memo 까지 — 태그 mode 도 거쳐가서 N+ 번 누르게 됨. 명확히
        # memo 도착할 때까지 반복.
        max_presses = 20
        for _ in range(max_presses):
            if es._active_col == es._memo_col_index() and es._tag_index is None:
                break
            es.action_next_column()
            await pilot.pause()
        assert es._active_col == es._memo_col_index()
        assert es._tag_index is None
        # ← → 마지막 태그 (인라인 limit 2 라 0..1 중 1 — 그러나 함수는 전체
        # tags list 의 마지막 인덱스를 사용. tags=[식비, 커피, 외식] 이라
        # _tag_index=2 가 됨 — 인라인 limit 와는 별개로 데이터상 마지막).
        es.action_prev_column()
        await pilot.pause()
        assert es._active_col == es._item_col_index()
        assert es._tag_index == 2


@pytest.mark.asyncio
async def test_row_change_resets_tag_mode():
    """↑/↓ 로 row 가 바뀌면 태그 모드 자동 종료."""
    _seed_local_tags({"e1": ["식비", "커피"], "e2": ["교통"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen._entry_tags.get("e1"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        for _ in range(6):  # 활성화 + col 0..4 + tag 0
            es.action_next_column()
            await pilot.pause()
        assert es._tag_index == 0
        # ↓ → row 1 → tag 모드 자동 종료
        await pilot.press("down")
        await pilot.pause()
        assert es._tag_index is None


# ---- 태그 선택 marker 색 / Enter 필터 ---------------------------------


@pytest.mark.asyncio
async def test_tag_marker_uses_cyan_style():
    """태그 모드 marker 는 일반 노란 marker 가 아닌 cyan."""
    _seed_local_tags({"e1": ["식비"]})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen._entry_tags.get("e1"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        for _ in range(6):  # 활성화 + col 0..4 + tag 0
            es.action_next_column()
            await pilot.pause()
        table = es.query_one("#entries-table", DataTable)
        item_cell = str(table.get_cell_at((0, 4)))
        assert "black on cyan" in item_cell
        # 일반 노란 마커는 아니어야
        assert "black on yellow" not in item_cell


@pytest.mark.asyncio
async def test_enter_on_tag_applies_tag_filter():
    """태그 위 Enter → 같은 태그가 붙은 entries 만 표시."""
    _seed_local_tags({"e1": ["식비"], "e2": ["식비"], "e3": []})
    fake = FakeClient(_sample_entries())
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        from whooing_tui.screens.entries import EntriesScreen
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.screen._entry_tags.get("e1"),
            timeout=3.0,
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]
        # row 0 = e1 (entry_date desc), 식비 태그 보유.
        for _ in range(6):
            es.action_next_column()
            await pilot.pause()
        assert es._tag_index == 0
        es.action_context_enter()
        await pilot.pause()
        # 필터 적용 — entries = [e1, e2] (식비 태그)
        assert es._active_filter is not None
        assert es._active_filter[0] == "tag"
        assert len(es._entries) == 2
        # status 안내
        assert "tag=#식비" in es.last_status
        # 'c' 로 해제 → 전체 복원
        es.action_clear_filter()
        await pilot.pause()
        assert es._active_filter is None
        assert len(es._entries) == 3
