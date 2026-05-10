"""p4_sync — db 자동 submit + 환경 감지 + silent 실패.

CL #51107+. 사용자 요청 명시:
- P4 환경이 갖춰져 있을 때만 자동 submit.
- 갖춰져 있지 않으면 데이터베이스 파일에 기록만 하고 *아무 에러메시지도
  보여주지 않아야* — 본 테스트는 표면화된 예외 / 메시지가 없음을 검증.
- description 은 *기계적* (LLM 미관여).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from whooing_tui import p4_sync


# ---- describe_annotation (mechanical) ----------------------------------


def test_describe_memo_only():
    out = p4_sync.describe_annotation(
        entry_id="e123", memo_changed=True, tags=None,
    )
    assert out == "[whooing-tui] entry e123 memo upsert"


def test_describe_tags_only():
    out = p4_sync.describe_annotation(
        entry_id="e123", memo_changed=False, tags=["식비"],
    )
    assert out == "[whooing-tui] entry e123 hashtags set [식비]"


def test_describe_memo_and_tags():
    out = p4_sync.describe_annotation(
        entry_id="e123", memo_changed=True, tags=["a", "b"],
    )
    assert out == "[whooing-tui] entry e123 memo upsert; hashtags set [a, b]"


def test_describe_deleted():
    out = p4_sync.describe_annotation(
        entry_id="e123", memo_changed=False, tags=None, deleted=True,
    )
    assert out == "[whooing-tui] entry e123 deleted"


def test_describe_noop_fallback():
    """memo 도 tags 도 없고 deleted 도 아니면 noop — 호출자 버그 노출용."""
    out = p4_sync.describe_annotation(
        entry_id="e123", memo_changed=False, tags=None,
    )
    assert out == "[whooing-tui] entry e123 noop"


# ---- is_p4_available — 환경 감지 ----------------------------------------


def test_is_p4_available_returns_false_when_bin_missing(monkeypatch):
    """`p4` 바이너리가 PATH 에도 없고 env override 도 없으면 False."""
    monkeypatch.delenv("WHOOING_P4_BIN", raising=False)
    with patch("whooing_tui.p4_sync.shutil.which", return_value=None):
        assert p4_sync.is_p4_available() is False


def test_is_p4_available_returns_false_when_p4_info_fails(monkeypatch, tmp_path):
    """가짜 p4 (always exit 1) → False."""
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text("#!/bin/sh\nexit 1\n")
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    assert p4_sync.is_p4_available() is False


def test_is_p4_available_true_when_p4_info_ok(monkeypatch, tmp_path):
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text("#!/bin/sh\nexit 0\n")
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    assert p4_sync.is_p4_available() is True


# ---- submit_db_to_p4 — silent 실패 ------------------------------------


def test_submit_silent_when_p4_missing(monkeypatch, tmp_path, caplog):
    """`p4` 부재 → 예외 없이 silent return. log 만 (debug)."""
    monkeypatch.delenv("WHOOING_P4_BIN", raising=False)
    with patch("whooing_tui.p4_sync.shutil.which", return_value=None):
        # blocking=True 로 worker 없이 즉시 실행 — exception 검증.
        p4_sync.submit_db_to_p4(
            tmp_path / "db.sqlite", "test desc", blocking=True,
        )
    # 사용자에게 표면화될 print/raise 없음 — 도달 자체가 검증.


def test_submit_silent_when_p4_where_fails(monkeypatch, tmp_path):
    """`p4 where` 실패 (workspace 매핑 외) → silent skip."""
    fake_p4 = tmp_path / "p4"
    # where 면 exit 1, 다른 명령은 0 — silent skip 흐름 검증.
    fake_p4.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"where\" ]; then exit 1; fi\n"
        "exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    p4_sync.submit_db_to_p4(db, "test", blocking=True)


def test_submit_async_threads_are_tracked_and_joinable(monkeypatch, tmp_path):
    """CL #51118+: daemon=False + _PENDING 추적 — `wait_for_pending` 으로
    모두 join 됨."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\nsleep 0.05\necho \"$@\" >> {log_file}\nexit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    # 비동기 submit 2개 발사 — _PENDING 에 추적되고 wait_for_pending 으로
    # 둘 다 끝까지 기다려야.
    p4_sync.submit_db_to_p4(db, "test 1", blocking=False)
    p4_sync.submit_db_to_p4(db, "test 2", blocking=False)
    p4_sync.wait_for_pending(timeout_per_thread=5.0)
    # 두 submit 의 호출이 모두 기록됐는지 확인.
    log_lines = log_file.read_text().splitlines()
    submit_calls = [l for l in log_lines if l.startswith("submit")]
    assert len(submit_calls) == 2


def test_wait_for_pending_when_empty_is_noop():
    """진행 중인 thread 가 없어도 예외 없이 즉시 return."""
    p4_sync.wait_for_pending()


def test_submit_runs_reconcile_and_submit_when_mapped(monkeypatch, tmp_path):
    """매핑돼 있으면 reconcile + submit 까지 도달. 호출 명령을 captured 로 검증."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    # where 는 0, 다른 모든 명령도 0 으로 + 호출 args 를 log 에 append.
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    desc = "[whooing-tui] entry e1 memo upsert"
    p4_sync.submit_db_to_p4(db, desc, blocking=True)
    log_lines = log_file.read_text().strip().splitlines()
    # where, reconcile, submit 세 호출이 있어야.
    assert any(line.startswith("where ") for line in log_lines)
    assert any("reconcile" in line for line in log_lines)
    assert any("submit -d" in line and desc in line for line in log_lines)
