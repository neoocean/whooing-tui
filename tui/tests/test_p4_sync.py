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


# ---- CL #51141+ (H10) hashtag diff in P4 description --------------------


def test_describe_with_added_tags():
    out = p4_sync.describe_annotation(
        entry_id="e1", memo_changed=False, tags=["식비", "외식"],
        previous_tags=["식비"],
    )
    assert out == "[whooing-tui] entry e1 hashtags set [식비, 외식] (+외식)"


def test_describe_with_removed_tags():
    out = p4_sync.describe_annotation(
        entry_id="e1", memo_changed=False, tags=["외식"],
        previous_tags=["식비", "외식"],
    )
    assert out == "[whooing-tui] entry e1 hashtags set [외식] (-식비)"


def test_describe_with_added_and_removed():
    out = p4_sync.describe_annotation(
        entry_id="e1", memo_changed=False, tags=["외식", "카페"],
        previous_tags=["식비", "외식"],
    )
    # 추가 (+카페) + 제거 (-식비). 순서: added 먼저, removed 나중.
    assert "+카페" in out
    assert "-식비" in out


def test_describe_no_diff_when_unchanged():
    out = p4_sync.describe_annotation(
        entry_id="e1", memo_changed=False, tags=["식비"],
        previous_tags=["식비"],
    )
    # 변경 없음 — 괄호 부분 없음.
    assert out == "[whooing-tui] entry e1 hashtags set [식비]"


def test_describe_previous_tags_none_disables_diff():
    """previous_tags=None — 종전 동작 (괄호 X)."""
    out = p4_sync.describe_annotation(
        entry_id="e1", memo_changed=False, tags=["식비"],
    )
    assert out == "[whooing-tui] entry e1 hashtags set [식비]"


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


def _make_fake_p4(path: Path, log_file: Path, *, where_filter_to: str | None = None,
                  sleep_before: float | None = None) -> None:
    """공통 fake p4 sh script. CL #52749+ numbered-CL 흐름 지원:

    - `change -i`: stdin 무시, "Change 90001 created." 출력 (parse 대상).
    - `opened -c <CL>`: 비빈 출력 (CL 에 파일 있다는 신호).
    - `where`: where_filter_to 지정 시 그 path arg 일 때만 0, 아니면 1.
    - 그 외: 모두 0.
    """
    sleep_clause = f"sleep {sleep_before}\n" if sleep_before else ""
    where_clause = ""
    if where_filter_to is not None:
        where_clause = (
            f'if [ "$1" = "where" ] && [ "$2" != "{where_filter_to}" ]; then\n'
            f"  exit 1\n"
            f"fi\n"
        )
    path.write_text(
        f"#!/bin/sh\n"
        f"{sleep_clause}"
        f'echo "$@" >> {log_file}\n'
        f"{where_clause}"
        f'case "$1" in\n'
        f'  change) [ "$2" = "-i" ] && echo "Change 90001 created." ;;\n'
        f'  opened) echo "//depot/x#1 - edit default change" ;;\n'
        f"esac\n"
        f"exit 0\n",
    )
    path.chmod(0o755)


def test_submit_async_threads_are_tracked_and_joinable(monkeypatch, tmp_path):
    """CL #51118+: daemon=False + _PENDING 추적 — `wait_for_pending` 으로
    모두 join 됨."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    _make_fake_p4(fake_p4, log_file, sleep_before=0.05)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    # 비동기 submit 2개 발사 — _PENDING 에 추적되고 wait_for_pending 으로
    # 둘 다 끝까지 기다려야.
    p4_sync.submit_db_to_p4(db, "test 1", blocking=False)
    p4_sync.submit_db_to_p4(db, "test 2", blocking=False)
    p4_sync.wait_for_pending(timeout_per_thread=5.0)
    # 두 submit 의 호출이 모두 기록됐는지 확인 — CL #52749+ 패턴은
    # `submit -c <CL>`.
    log_lines = log_file.read_text().splitlines()
    submit_calls = [l for l in log_lines if l.startswith("submit ")]
    assert len(submit_calls) == 2


def test_wait_for_pending_when_empty_is_noop():
    """진행 중인 thread 가 없어도 예외 없이 즉시 return."""
    p4_sync.wait_for_pending()


# ---- CL #51119+: sync_db_from_p4 / flush_on_exit ------------------------


def test_sync_silent_when_p4_missing(monkeypatch, tmp_path):
    """`p4` 부재 → 예외 없이 silent return."""
    monkeypatch.delenv("WHOOING_P4_BIN", raising=False)
    with patch("whooing_tui.p4_sync.shutil.which", return_value=None):
        p4_sync.sync_db_from_p4(tmp_path / "db.sqlite")


def test_sync_silent_when_not_in_p4_workspace(monkeypatch, tmp_path):
    """`p4 where` 가 0 이 아닌 경우 (매핑 외) → silent skip, sync 미실행."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f"if [ \"$1\" = \"where\" ]; then exit 1; fi\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    p4_sync.sync_db_from_p4(db)
    log = log_file.read_text().splitlines()
    # where 만 호출되고 sync 는 호출되지 않아야.
    assert any(line.startswith("where ") for line in log)
    assert not any(line.startswith("sync ") for line in log)


def test_sync_runs_p4_sync_when_mapped(monkeypatch, tmp_path):
    """매핑 OK → `p4 sync <db>` 실행."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\necho \"$@\" >> {log_file}\nexit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    p4_sync.sync_db_from_p4(db)
    log = log_file.read_text().splitlines()
    # where + sync 양쪽 호출.
    assert any(line.startswith("where ") for line in log)
    sync_calls = [l for l in log if l.startswith("sync ")]
    assert len(sync_calls) == 1
    assert str(db) in sync_calls[0]


def test_flush_on_exit_waits_then_submits(monkeypatch, tmp_path):
    """flush_on_exit 가 wait_for_pending → blocking submit 순서로 호출."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    _make_fake_p4(fake_p4, log_file)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    p4_sync.flush_on_exit(db)
    log = log_file.read_text().splitlines()
    # where + reconcile + submit 모두 호출 (blocking submit 흐름).
    # CL #52749+ 패턴: change -i → reconcile -c → submit -c.
    assert any(l.startswith("where ") for l in log)
    assert any("reconcile" in l for l in log)
    assert any(l.startswith("submit -c ") for l in log)


def test_submit_runs_reconcile_and_submit_when_mapped(monkeypatch, tmp_path):
    """매핑돼 있으면 reconcile + submit 까지 도달. 호출 명령을 captured 로 검증.

    CL #52749+ 패턴: where → change -i → reconcile -c <CL> → opened -c →
    submit -c <CL>. desc 는 `change -i` 의 stdin 으로 전달되므로 log args
    에는 안 보임. submit 자체 args 는 `-c <CL>` 만.
    """
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    _make_fake_p4(fake_p4, log_file)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    desc = "[whooing-tui] entry e1 memo upsert"
    p4_sync.submit_db_to_p4(db, desc, blocking=True)
    log_lines = log_file.read_text().strip().splitlines()
    # where → change -i → reconcile → opened → submit 5 단계.
    assert any(l.startswith("where ") for l in log_lines)
    assert any(l.startswith("change -i") for l in log_lines)
    assert any("reconcile" in l for l in log_lines)
    assert any(l.startswith("submit -c ") for l in log_lines)


# ---- CL #51124+ 첨부 description / 다중 파일 submit ---------------------


def test_describe_attachment_add_full_meta():
    out = p4_sync.describe_attachment_add(
        entry_id="e123", filename="invoice.pdf",
        size_bytes=12345, sha256="ab12cd34ef567890" * 4,  # 64 chars
    )
    assert out == (
        "[whooing-tui] entry e123 attachment add: invoice.pdf "
        "(12345 bytes, sha256=ab12cd34)"
    )


def test_describe_attachment_add_no_size_no_sha():
    """defensive — caller 가 stat 못 한 경우 size/sha 부분 생략."""
    out = p4_sync.describe_attachment_add(
        entry_id="e1", filename="x.pdf", size_bytes=None, sha256=None,
    )
    assert out == "[whooing-tui] entry e1 attachment add: x.pdf"


def test_describe_attachment_add_size_only():
    out = p4_sync.describe_attachment_add(
        entry_id="e1", filename="x.pdf", size_bytes=100, sha256=None,
    )
    assert out == "[whooing-tui] entry e1 attachment add: x.pdf (100 bytes)"


def test_describe_attachment_delete_simple():
    out = p4_sync.describe_attachment_delete(
        entry_id="e123", filename="invoice.pdf",
    )
    assert out == "[whooing-tui] entry e123 attachment delete: invoice.pdf"


def test_describe_attachment_delete_dedup_kept():
    """dedup 으로 디스크 파일이 보존되면 description 에 명시."""
    out = p4_sync.describe_attachment_delete(
        entry_id="e123", filename="invoice.pdf", kept_other_refs=2,
    )
    assert out == (
        "[whooing-tui] entry e123 attachment delete "
        "(db only, file kept — 2 other refs): invoice.pdf"
    )


def test_submit_files_to_p4_passes_all_paths_to_reconcile_and_submit(
    monkeypatch, tmp_path,
):
    """다중 파일 submit — 한 numbered CL 으로 묶임. 사용자 요청 CL #51124.

    CL #52749+: reconcile 의 args 에 모든 path + `-c <CL>`, submit 은
    `-c <CL>` 만 (path X — submit 의 file arg 는 절대 path 못 받음).
    """
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    _make_fake_p4(fake_p4, log_file)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    att = tmp_path / "x.pdf"
    att.write_bytes(b"PDF")
    desc = "[whooing-tui] entry e1 attachment add: x.pdf (3 bytes, sha256=abcd1234)"
    p4_sync.submit_files_to_p4([db, att], desc, blocking=True)
    lines = log_file.read_text().strip().splitlines()
    # 두 path 모두에 where 호출.
    where_lines = [l for l in lines if l.startswith("where ")]
    assert len(where_lines) == 2
    assert any(str(db) in l for l in where_lines)
    assert any(str(att) in l for l in where_lines)
    # reconcile 한 번에 두 path 다 + `-c <CL>` 옵션.
    reconcile = next(l for l in lines if "reconcile" in l)
    assert "-c " in reconcile
    assert str(db) in reconcile
    assert str(att) in reconcile
    # submit 은 `-c <CL>` — paths 안 들어감 (P4 syntax 제약).
    submit = next(l for l in lines if l.startswith("submit "))
    assert submit.startswith("submit -c ")


def test_submit_files_to_p4_skips_unmapped_paths(monkeypatch, tmp_path):
    """매핑 안 된 path 는 silent skip — 매핑된 path 만 reconcile 인자."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    att = tmp_path / "x.pdf"
    att.write_bytes(b"PDF")
    _make_fake_p4(fake_p4, log_file, where_filter_to=str(db))
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    p4_sync.submit_files_to_p4([db, att], "test", blocking=True)
    lines = log_file.read_text().strip().splitlines()
    # reconcile 에 db 만 포함, att 는 빠짐. submit 은 `-c <CL>` 만 (paths 없음).
    reconcile = next(l for l in lines if "reconcile" in l)
    assert str(db) in reconcile
    assert str(att) not in reconcile
    submit = next(l for l in lines if l.startswith("submit "))
    assert submit.startswith("submit -c ")


def test_submit_files_to_p4_silent_when_no_paths_mapped(monkeypatch, tmp_path):
    """모든 path 가 매핑 외 → reconcile/submit 호출 없이 silent return."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f"if [ \"$1\" = \"where\" ]; then exit 1; fi\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    p4_sync.submit_files_to_p4(
        [tmp_path / "db.sqlite", tmp_path / "x.pdf"], "test", blocking=True,
    )
    lines = log_file.read_text().splitlines()
    # tmp_path 자체에 'submit'/'reconcile' 부분 문자열이 들어갈 수 있어 (테스트
    # 이름 기반) 정확한 명령 prefix 로 검사. p4 호출 첫 인자가 명령 이름.
    assert all(not l.startswith("reconcile ") for l in lines)
    assert all(not l.startswith("submit ") for l in lines)


def test_submit_files_to_p4_empty_list_is_noop(monkeypatch, tmp_path):
    """paths 가 빈 리스트 — p4 실행 자체 X."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(f"#!/bin/sh\necho \"$@\" >> {log_file}\nexit 0\n")
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    p4_sync.submit_files_to_p4([], "test", blocking=True)
    # 빈 list → bin 호출조차 없음.
    assert not log_file.exists() or log_file.read_text() == ""


def test_submit_db_to_p4_still_works_via_files_wrapper(monkeypatch, tmp_path):
    """submit_db_to_p4 가 submit_files_to_p4 의 1-원소 wrapper 로 동작 — 기존
    호출자 (entries 의 _persist_local) 회귀 보호."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    _make_fake_p4(fake_p4, log_file)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    p4_sync.submit_db_to_p4(db, "[whooing-tui] entry e1 memo upsert", blocking=True)
    lines = log_file.read_text().strip().splitlines()
    # CL #52749+: submit 은 `-c <CL>`, db path 는 reconcile 에만.
    assert any(l.startswith("submit -c ") for l in lines)
    reconcile = next(l for l in lines if "reconcile" in l)
    assert str(db) in reconcile


# ---- CL #51136+ (A4) on_complete callback ------------------------------


def test_submit_callback_invoked_with_status_no_p4(monkeypatch, tmp_path):
    """p4 부재 → callback 이 'no-p4' 로 호출."""
    monkeypatch.delenv("WHOOING_P4_BIN", raising=False)
    statuses = []
    with patch("whooing_tui.p4_sync.shutil.which", return_value=None):
        p4_sync.submit_files_to_p4(
            [tmp_path / "x.sqlite"], "test",
            blocking=True, on_complete=statuses.append,
        )
    assert statuses == ["no-p4"]


def test_submit_callback_invoked_with_status_unmapped(monkeypatch, tmp_path):
    """매핑 외 → 'unmapped'."""
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text("#!/bin/sh\nif [ \"$1\" = \"where\" ]; then exit 1; fi\nexit 0\n")
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    statuses = []
    p4_sync.submit_files_to_p4(
        [tmp_path / "x.sqlite"], "test",
        blocking=True, on_complete=statuses.append,
    )
    assert statuses == ["unmapped"]


def test_submit_callback_invoked_with_status_ok(monkeypatch, tmp_path):
    """모든 단계 OK → 'ok'.

    CL #52749+: numbered CL 흐름이라 fake_p4 가 `change -i` 응답으로 CL
    번호를 출력해야 + `opened -c <CL>` 가 비빈 출력이어야 status = "ok".
    """
    fake_p4 = tmp_path / "p4"
    log_file = tmp_path / "p4-calls.txt"
    _make_fake_p4(fake_p4, log_file)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    statuses = []
    db = tmp_path / "x.sqlite"
    db.write_bytes(b"")
    p4_sync.submit_files_to_p4(
        [db], "test", blocking=True, on_complete=statuses.append,
    )
    assert statuses == ["ok"]


def test_submit_callback_with_empty_paths_returns_noop(tmp_path):
    """paths 빈 list → 'noop'."""
    statuses = []
    p4_sync.submit_files_to_p4(
        [], "test", blocking=True, on_complete=statuses.append,
    )
    assert statuses == ["noop"]


# ---- CL #52749+ : submit 절대경로-인자 회귀 방지 + numbered CL 정책 -----


def test_submit_uses_numbered_cl_not_default():
    """`p4_sync._do_submit_multi` source 가 numbered CL 패턴인지 검증.

    종전 (≤0.53.3) 버그: `p4 submit -d <desc> <local-abs-paths>` 호출 —
    P4 submit 의 file arg 는 절대 경로 syntax 를 안 받아 'Usage:' 에러로
    fail. 사용자 보고로 발견 (CL #52749). 이 검증은 source 안에 새 패턴
    keyword 가 있는지로 회귀 방지.
    """
    import inspect

    from whooing_tui import p4_sync

    src = inspect.getsource(p4_sync._do_submit_multi)
    # numbered CL 만들고 거기에 submit
    assert "_create_numbered_change" in src, (
        "_do_submit_multi 가 numbered CL 패턴 (회귀 방지)"
    )
    # submit -c <CL> 호출 — paths 없음
    assert '"submit", "-c", cl' in src or "['submit', '-c', cl" in src, (
        "submit 은 -c <CL> 만 — 절대 경로 paths 전달 X"
    )


def test_create_numbered_change_helper_exists():
    """CL 생성 helper 가 export 되어 다른 호출자도 같은 정책 따를 수 있도록."""
    from whooing_tui import p4_sync
    assert hasattr(p4_sync, "_create_numbered_change")


# ---- CL #52832+ : startup db freshness helpers --------------------------


def test_has_pending_local_changes_false_when_no_p4(tmp_path):
    """P4 환경 부재 → False (silent)."""
    with patch("whooing_tui.p4_sync.shutil.which", return_value=None):
        assert p4_sync.has_pending_local_changes(tmp_path / "db.sqlite") is False


def test_is_outdated_vs_p4_false_when_no_p4(tmp_path):
    with patch("whooing_tui.p4_sync.shutil.which", return_value=None):
        assert p4_sync.is_outdated_vs_p4(tmp_path / "db.sqlite") is False


def test_has_pending_local_changes_false_when_unmapped(monkeypatch, tmp_path):
    """`where` 가 non-zero (매핑 외) → False, reconcile 호출도 안 함."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f"if [ \"$1\" = \"where\" ]; then exit 1; fi\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    result = p4_sync.has_pending_local_changes(db)
    assert result is False
    log = log_file.read_text().splitlines()
    assert not any("reconcile" in line for line in log)


def test_has_pending_local_changes_true_when_reconcile_outputs_line(
    monkeypatch, tmp_path,
):
    """`reconcile -n` 의 stdout 에 변경 후보 한 줄 → True."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f'if [ "$1" = "reconcile" ]; then\n'
        f"  echo \"//depot/db.sqlite#3 - edit\"\n"
        f"  exit 0\n"
        f"fi\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    assert p4_sync.has_pending_local_changes(db) is True


def test_has_pending_local_changes_false_when_reconcile_empty(
    monkeypatch, tmp_path,
):
    """`reconcile -n` 의 stdout 이 비어있음 → False."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\necho \"$@\" >> {log_file}\nexit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    assert p4_sync.has_pending_local_changes(db) is False


def test_is_outdated_vs_p4_false_when_up_to_date_message(
    monkeypatch, tmp_path,
):
    """`sync -n` 이 'file(s) up-to-date' 출력 → False (최신)."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f'if [ "$1" = "sync" ]; then\n'
        f"  echo \"file(s) up-to-date.\" >&2\n"
        f"fi\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    assert p4_sync.is_outdated_vs_p4(db) is False


def test_is_outdated_vs_p4_true_when_sync_would_update(monkeypatch, tmp_path):
    """`sync -n` 이 sync 후보를 출력 → True (오래됨)."""
    log_file = tmp_path / "p4-calls.txt"
    fake_p4 = tmp_path / "p4"
    fake_p4.write_text(
        f"#!/bin/sh\n"
        f"echo \"$@\" >> {log_file}\n"
        f'if [ "$1" = "sync" ]; then\n'
        f"  echo \"//depot/db.sqlite#5 - updating\"\n"
        f"fi\n"
        f"exit 0\n",
    )
    fake_p4.chmod(0o755)
    monkeypatch.setenv("WHOOING_P4_BIN", str(fake_p4))
    db = tmp_path / "db.sqlite"
    db.write_bytes(b"")
    assert p4_sync.is_outdated_vs_p4(db) is True
