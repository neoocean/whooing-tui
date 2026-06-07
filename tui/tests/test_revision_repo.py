"""EntryRevisionRepository — 수정 이력/소프트삭제(안 B) TUI 어댑터 통합 테스트.

conftest 의 autouse fixture 가 WHOOING_DATA_DIR 을 tmp 로 격리하므로 실제
사용자 db 를 건드리지 않는다. P4 submit 은 tmp 가 워크스페이스 밖이라 silent.
"""

from __future__ import annotations

import pytest

from whooing_tui import data as tui_data
from whooing_tui.revision_repo import EntryRevisionRepository


@pytest.fixture
def repo():
    tui_data.init_shared_schema()  # WHOOING_DATA_DIR 격리 → tmp db + v10 스키마.
    return EntryRevisionRepository()


def _entry(eid, *, date="20260601", money=30000, item="저녁",
           left="x50", right="x80", memo=""):
    return {
        "entry_id": eid, "entry_date": date, "money": money,
        "l_account": "expenses", "l_account_id": left,
        "r_account": "liabilities", "r_account_id": right,
        "item": item, "memo": memo,
    }


def test_record_create_starts_history(repo):
    lid = repo.record_create(entry=_entry("1001"), section_id="s1")
    assert lid == "1001"
    revs = repo.revisions_for(lid)
    assert [r["op"] for r in revs] == ["create"]
    assert repo.logical_for_entry("1001") == "1001"


def test_record_edit_seeds_baseline_and_appends(repo):
    # baseline 없이 곧장 수정 → ensure_baseline 이 create 깔고 edit 추가.
    lid = repo.record_edit(
        prev=_entry("1001", money=30000, item="저녁"),
        new=_entry("1001", money=27000, item="저녁(닭칼국수)"),
        section_id="s1",
    )
    assert lid == "1001"
    revs = repo.revisions_for(lid)
    assert [r["op"] for r in revs] == ["create", "edit"]
    assert "money 30,000→27,000" in (revs[-1]["note"] or "")
    head = repo.head("1001")
    assert head["current_entry_id"] == "1001" and head["is_deleted"] == 0


def test_record_delete_then_restore_new_id(repo):
    repo.record_create(entry=_entry("1001"), section_id="s1")
    lid = repo.record_delete(entry=_entry("1001"), section_id="s1")
    assert lid == "1001"
    trash = repo.list_deleted("s1")
    assert [t["logical_id"] for t in trash] == ["1001"]
    assert trash[0]["money"] == 30000
    head = repo.head("1001")
    assert head["is_deleted"] == 1 and head["current_entry_id"] is None

    # 복원: 후잉 재생성 → 새 entry_id 2002.
    rev_no = repo.record_restore(
        logical_id="1001", new_entry=_entry("2002"), section_id="s1",
        reverted_from=1,
    )
    assert rev_no == 3
    assert repo.list_deleted("s1") == []
    assert repo.logical_for_entry("2002") == "1001"
    assert [r["op"] for r in repo.revisions_for("1001")] == [
        "create", "delete", "restore"
    ]


def test_purge_removes_history(repo):
    repo.record_create(entry=_entry("1001"), section_id="s1")
    repo.record_delete(entry=_entry("1001"), section_id="s1")
    repo.purge("1001")
    assert repo.revisions_for("1001") == []
    assert repo.head("1001") is None
