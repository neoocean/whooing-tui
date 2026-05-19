"""DupeScanRepository — save / load / update_status / clear / has_open_scan
round-trips (CL #52989+).

격리: pytest 의 `tmp_path` + `WHOOING_DATA_DIR` env override 로 본 테스트
프로세스의 sqlite 가 별도 디렉토리. 다른 테스트와 간섭 없음.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from whooing_core.dupes import DupeCluster

from whooing_tui import data as tui_data
from whooing_tui.dupe_scan_repo import DupeScanRepository, StoredCluster


@pytest.fixture
def db_env(tmp_path, monkeypatch):
    """sqlite db 를 tmp_path 안으로 — 각 테스트 격리."""
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path))
    # 스키마 init (init_shared_schema 가 dupe_scan_clusters 테이블 생성).
    tui_data.init_shared_schema()
    yield tmp_path


def _cluster(eids: list[str], **kw) -> DupeCluster:
    """짧은 fixture — entries 는 entry_id 만 있는 minimal dict."""
    entries = tuple({"entry_id": e, "entry_date": "20260510", "money": 1000,
                     "item": "x"} for e in eids)
    return DupeCluster(
        entries=entries,
        verdict=kw.get("verdict", "identical"),
        reasons=tuple(kw.get("reasons", ())),
        keep_suggestion=kw.get("keep_suggestion", eids[0] if eids else None),
    )


def test_save_and_load_round_trip(db_env):
    repo = DupeScanRepository()
    c1 = _cluster(["a", "b"], verdict="identical",
                  reasons=("모든 핵심 필드 일치",))
    c2 = _cluster(["c", "d"], verdict="very_likely",
                  reasons=("좌/우 계정이 반대",))
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[c1, c2],
    )
    assert len(stored) == 2
    assert all(s.id > 0 for s in stored)
    # 동일 range 로 load_open_clusters → 같은 2건.
    loaded = repo.load_open_clusters(
        section_id="s1", range_start="20230520", range_end="20260520",
    )
    assert len(loaded) == 2
    # 정렬 — identical 먼저.
    assert loaded[0].verdict == "identical"
    assert loaded[1].verdict == "very_likely"
    assert {e["entry_id"] for e in loaded[0].entries} == {"a", "b"}


def test_load_returns_empty_for_different_range(db_env):
    repo = DupeScanRepository()
    repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a", "b"])],
    )
    # 다른 range — 0건.
    loaded = repo.load_open_clusters(
        section_id="s1", range_start="20240101", range_end="20260520",
    )
    assert loaded == []


def test_load_returns_empty_for_different_section(db_env):
    repo = DupeScanRepository()
    repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a", "b"])],
    )
    loaded = repo.load_open_clusters(
        section_id="s2", range_start="20230520", range_end="20260520",
    )
    assert loaded == []


def test_update_status_resolves(db_env):
    repo = DupeScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a", "b"]), _cluster(["c", "d"])],
    )
    repo.update_status(stored[0].id, "resolved")
    # 1건만 pending.
    open_now = repo.load_open_clusters(
        section_id="s1", range_start="20230520", range_end="20260520",
    )
    assert len(open_now) == 1
    # resolved 도 status='resolved' 로 load_all 에서 보임.
    all_now = repo.load_all_clusters(
        section_id="s1", range_start="20230520", range_end="20260520",
    )
    assert len(all_now) == 2
    resolved = next(c for c in all_now if c.status == "resolved")
    assert resolved.id == stored[0].id


def test_update_status_skipped_also_excluded_from_open(db_env):
    """skipped 도 사용자 의도 (중복 아님) 라 pending 에서 제외."""
    repo = DupeScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a", "b"])],
    )
    repo.update_status(stored[0].id, "skipped")
    assert repo.has_open_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
    ) is False
    assert repo.load_open_clusters(
        section_id="s1", range_start="20230520", range_end="20260520",
    ) == []


def test_update_status_invalid_raises(db_env):
    repo = DupeScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a"])],
    )
    with pytest.raises(ValueError):
        repo.update_status(stored[0].id, "garbage")


def test_clear_scan_removes_all_statuses(db_env):
    """refresh 동작 — pending / resolved / skipped 모두 지움."""
    repo = DupeScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a"]), _cluster(["b"]), _cluster(["c"])],
    )
    repo.update_status(stored[0].id, "resolved")
    repo.update_status(stored[1].id, "skipped")
    removed = repo.clear_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
    )
    assert removed == 3
    assert repo.has_open_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
    ) is False


def test_clear_scan_only_targets_same_range(db_env):
    repo = DupeScanRepository()
    repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a"])],
    )
    repo.save_scan(
        section_id="s1", range_start="20240101", range_end="20260520",
        clusters=[_cluster(["b"])],
    )
    # 첫 range 만 clear.
    repo.clear_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
    )
    assert repo.load_open_clusters(
        section_id="s1", range_start="20240101", range_end="20260520",
    )  # 두번째 range 살아남음.


def test_has_open_scan_true_then_false_after_resolve_all(db_env):
    repo = DupeScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[_cluster(["a"]), _cluster(["b"])],
    )
    assert repo.has_open_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
    ) is True
    repo.update_status(stored[0].id, "resolved")
    repo.update_status(stored[1].id, "resolved")
    assert repo.has_open_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
    ) is False


def test_save_empty_clusters_is_noop(db_env):
    repo = DupeScanRepository()
    stored = repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[],
    )
    assert stored == []


def test_load_preserves_entry_dict_shape(db_env):
    """entries 가 raw whooing dict shape 그대로 round-trip — 확장 필드 보존."""
    repo = DupeScanRepository()
    rich = DupeCluster(
        entries=({
            "entry_id": "x1", "entry_date": "20260510", "money": 5000,
            "l_account_id": "x20", "r_account_id": "x11",
            "item": "스타벅스", "memo": "점심", "custom_extra": "preserve me",
        },),
        verdict="identical", reasons=("동일",), keep_suggestion="x1",
    )
    repo.save_scan(
        section_id="s1", range_start="20230520", range_end="20260520",
        clusters=[rich],
    )
    loaded = repo.load_open_clusters(
        section_id="s1", range_start="20230520", range_end="20260520",
    )
    assert len(loaded) == 1
    e = loaded[0].entries[0]
    assert e["custom_extra"] == "preserve me"
    assert e["item"] == "스타벅스"
