"""시나리오 11 통합 — 휴지통/이력 화면 dismiss + 삭제→복원 라운드트립.

conftest autouse 가 WHOOING_DATA_DIR 격리. EntriesScreen 워커를 직접 호출해
후잉 mutation(fake) + 로컬 이력 기록을 검증한다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.entries import EntriesScreen
from whooing_tui.screens.trash import TrashScreen
from whooing_tui.screens.revision_history import RevisionHistoryScreen


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


class FakeClient:
    def __init__(self, entries):
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {
            "expenses": [{"account_id": "x20", "title": "식비", "type": "expenses"}],
            "assets": [{"account_id": "x11", "title": "현금", "type": "assets"}],
        }
        self.entries = {"s1": list(entries)}
        self._next = 100
        self.create_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.delete_calls: list[str] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self.entries.get(section_id, []))

    async def create_entry(self, *, section_id, l_account, l_account_id,
                           r_account, r_account_id, money, item, memo,
                           entry_date=None):
        self._next += 1
        eid = f"n{self._next}"
        self.create_calls.append({"entry_id": eid, "money": money, "item": item})
        self.entries.setdefault(section_id, []).append({
            "entry_id": eid, "entry_date": entry_date, "money": money,
            "l_account": l_account, "l_account_id": l_account_id,
            "r_account": r_account, "r_account_id": r_account_id,
            "item": item, "memo": memo,
        })
        return {"status": "ok", "entry_id": eid}

    async def update_entry(self, *, section_id, entry_id, **kw):
        self.update_calls.append({"entry_id": entry_id, **kw})
        for e in self.entries.get(section_id, []):
            if str(e.get("entry_id")) == str(entry_id):
                e.update({k: v for k, v in kw.items() if v is not None})
        return {"status": "ok"}

    async def delete_entry(self, *, section_id, entry_id, entry_date=None):
        self.delete_calls.append(str(entry_id))
        self.entries[section_id] = [
            e for e in self.entries.get(section_id, [])
            if str(e.get("entry_id")) != str(entry_id)
        ]
        return {"status": "ok"}


def _entry(eid="e1", money=12000, item="스타벅스"):
    return {
        "entry_id": eid, "entry_date": "20260510", "money": money,
        "l_account": "expenses", "l_account_id": "x20",
        "r_account": "assets", "r_account_id": "x11",
        "item": item, "memo": "오후",
    }


async def _boot(fake):
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    return app


# ---- 화면 dismiss 값 ----------------------------------------------------


@pytest.mark.asyncio
async def test_trash_screen_restore_dismiss():
    captured = []
    app = await _boot(FakeClient([_entry()]))
    async with app.run_test() as pilot:
        await _wait_for(lambda: isinstance(app.screen, EntriesScreen))
        deleted = [{"logical_id": "e1", "entry_date": "20260510",
                    "money": 12000, "item": "스타벅스", "deleted_at": "2026-06-07"}]
        app.push_screen(TrashScreen(deleted), captured.append)
        await pilot.pause()
        scr = app.screen
        scr.query_one("#trash-list").highlighted = 0
        scr.action_restore()
        await pilot.pause()
    assert captured == [("restore", "e1")]


@pytest.mark.asyncio
async def test_revision_history_revert_dismiss():
    captured = []
    app = await _boot(FakeClient([_entry()]))
    async with app.run_test() as pilot:
        await _wait_for(lambda: isinstance(app.screen, EntriesScreen))
        revs = [
            {"revision_no": 1, "op": "create", "created_at": "2026-06-01T10:00",
             "entry_date": "20260510", "money": 12000, "item": "스타벅스",
             "l_account": "expenses", "l_account_id": "x20",
             "r_account": "assets", "r_account_id": "x11", "memo": ""},
            {"revision_no": 2, "op": "edit", "created_at": "2026-06-02T10:00",
             "entry_date": "20260510", "money": 9000, "item": "스타벅스",
             "l_account": "expenses", "l_account_id": "x20",
             "r_account": "assets", "r_account_id": "x11", "memo": "",
             "note": "money 12,000→9,000"},
        ]
        app.push_screen(RevisionHistoryScreen(revs, logical_id="e1"),
                        captured.append)
        await pilot.pause()
        scr = app.screen
        # 최신이 위 → index 0 = v2. v1 로 되돌리려면 index 1 선택.
        scr.query_one("#rev-list").highlighted = 1
        scr.action_revert()
        await pilot.pause()
    assert captured == [("revert", 1)]


# ---- 삭제 → 휴지통 → 복원 라운드트립 -----------------------------------


@pytest.mark.asyncio
async def test_delete_then_restore_roundtrip():
    fake = FakeClient([_entry("e1")])
    app = await _boot(fake)
    async with app.run_test() as pilot:
        await _wait_for(
            lambda: isinstance(app.screen, EntriesScreen)
            and app.session.section_id == "s1"
        )
        es: EntriesScreen = app.screen  # type: ignore[assignment]

        # 삭제 (워커) — 모달 우회하고 직접.
        es._submit_delete(_entry("e1"))
        assert await _wait_for(lambda: "e1" in fake.delete_calls)
        assert await _wait_for(
            lambda: [d["logical_id"] for d in es._rev_repo.list_deleted("s1")] == ["e1"]
        )

        # 복원 (워커) — 후잉 재생성 → 새 id.
        es._restore_logical("e1")
        assert await _wait_for(lambda: len(fake.create_calls) == 1)
        new_eid = fake.create_calls[0]["entry_id"]
        # 같은 logical 로 매핑 + 휴지통 비워짐 + 이력 op 순서.
        assert await _wait_for(
            lambda: es._rev_repo.logical_for_entry(new_eid) == "e1"
        )
        assert es._rev_repo.list_deleted("s1") == []
        ops = [r["op"] for r in es._rev_repo.revisions_for("e1")]
        assert ops == ["create", "delete", "restore"]
