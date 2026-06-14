"""sync facade — 백엔드 선택 + no-op/위임 동작 단위 테스트.

코어는 `sync` facade 만 호출하고 P4 를 직접 모른다. 기본 백엔드는 'none'
(동기화 안 함) — 모든 동작 no-op, 질의는 안전한 기본값. 'p4' 로 켜면
`p4_sync` 로 위임한다. (env WHOOING_SYNC_BACKEND > config [sync] backend > none)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_tui import sync


@pytest.fixture(autouse=True)
def _reset_sync():
    sync.reset()
    yield
    sync.reset()


class _Cfg:
    def __init__(self, backend):
        self.sync_backend = backend


# ---- 백엔드 결정 우선순위 -------------------------------------------------

def test_resolve_defaults_to_none(monkeypatch):
    monkeypatch.delenv("WHOOING_SYNC_BACKEND", raising=False)
    assert sync.resolve() == "none"
    assert sync.resolve(_Cfg(None)) == "none"


def test_resolve_config_used_when_no_env(monkeypatch):
    monkeypatch.delenv("WHOOING_SYNC_BACKEND", raising=False)
    assert sync.resolve(_Cfg("p4")) == "p4"


def test_resolve_env_overrides_config(monkeypatch):
    monkeypatch.setenv("WHOOING_SYNC_BACKEND", "none")
    assert sync.resolve(_Cfg("p4")) == "none"
    monkeypatch.setenv("WHOOING_SYNC_BACKEND", "p4")
    assert sync.resolve(_Cfg("none")) == "p4"


def test_resolve_invalid_falls_back_to_none(monkeypatch):
    monkeypatch.setenv("WHOOING_SYNC_BACKEND", "svn")
    assert sync.resolve(_Cfg("p4")) == "none"


def test_configure_and_is_enabled():
    sync.configure("none")
    assert sync.active_backend() == "none"
    assert sync.is_enabled() is False
    sync.configure("p4")
    assert sync.active_backend() == "p4"
    assert sync.is_enabled() is True
    # 알 수 없는 값 → none 으로 강등.
    sync.configure("bogus")
    assert sync.active_backend() == "none"


def test_active_backend_uses_env_when_not_configured(monkeypatch):
    sync.reset()
    monkeypatch.setenv("WHOOING_SYNC_BACKEND", "p4")
    assert sync.active_backend() == "p4"
    assert sync.is_enabled() is True


# ---- 'none' 백엔드: no-op / 안전한 기본값 --------------------------------

def test_none_backend_actions_are_noop(monkeypatch):
    """백엔드 none 이면 어떤 p4_sync 함수도 호출되지 않는다."""
    sync.configure("none")
    from whooing_tui import p4_sync

    called = []
    for name in (
        "submit_db_to_p4", "submit_files_to_p4", "sync_db_from_p4",
        "has_pending_local_changes", "is_outdated_vs_p4",
        "mark_session_mutated", "flush_on_exit", "wait_for_pending",
        "pending_count",
    ):
        monkeypatch.setattr(
            p4_sync, name,
            lambda *a, _n=name, **k: called.append(_n),
        )

    db = Path("/tmp/whooing-data.sqlite")
    sync.submit_db(db, "desc")
    sync.submit_files([db], "desc")
    sync.sync_on_startup(db)
    sync.mark_session_mutated()
    sync.flush_on_exit(db)
    sync.wait_for_pending()
    assert sync.startup_has_pending(db) is False
    assert sync.startup_is_outdated(db) is False
    assert sync.pending_count() == 0
    assert called == []


def test_none_backend_submit_files_skips_on_complete():
    """비활성 시 on_complete 도 호출 안 함 — 비-P4 사용자에게 알림 없음."""
    sync.configure("none")
    hits = []
    sync.submit_files([Path("x")], "d", on_complete=lambda s: hits.append(s))
    assert hits == []


# ---- 'p4' 백엔드: p4_sync 위임 -------------------------------------------

def test_p4_backend_delegates(monkeypatch):
    sync.configure("p4")
    from whooing_tui import p4_sync

    calls = {}
    monkeypatch.setattr(
        p4_sync, "submit_db_to_p4",
        lambda db, desc, *, blocking=False: calls.setdefault("db", (db, desc, blocking)),
    )
    monkeypatch.setattr(
        p4_sync, "submit_files_to_p4",
        lambda paths, desc, *, blocking=False, on_complete=None:
            calls.setdefault("files", (list(paths), desc, blocking, on_complete)),
    )
    monkeypatch.setattr(
        p4_sync, "has_pending_local_changes", lambda p: True,
    )
    monkeypatch.setattr(p4_sync, "is_outdated_vs_p4", lambda p: True)
    monkeypatch.setattr(p4_sync, "pending_count", lambda: 3)

    db = Path("/tmp/db.sqlite")
    sync.submit_db(db, "d1")
    sync.submit_files([db], "d2", on_complete="cb")
    assert calls["db"] == (db, "d1", False)
    assert calls["files"] == ([db], "d2", False, "cb")
    assert sync.startup_has_pending(db) is True
    assert sync.startup_is_outdated(db) is True
    assert sync.pending_count() == 3


# ---- describe_* 는 백엔드 무관하게 순수 위임 -----------------------------

def test_describe_helpers_work_regardless_of_backend():
    sync.configure("none")
    s = sync.describe_annotation(
        entry_id="e1", memo_changed=True, tags=["식비"],
    )
    assert "e1" in s and s.startswith("[whooing-tui]")
    add = sync.describe_attachment_add(
        entry_id="e1", filename="r.pdf", size_bytes=10, sha256="abcdef0123",
    )
    assert "r.pdf" in add
