"""P4 자동 submit — 로컬 db 가 변경될 때 백그라운드로 Perforce 에 제출.

CL #51107+. 사용자 요청:

> whooing-tui 실행 중 데이터베이스에 변경이 발생하는 이벤트가 일어나면
> 데이터베이스 파일을 서브밋해주세요. 서브밋할 때 디스크립션에 무엇이
> 변경되었는지 기록해야 합니다. 이 때는 LLM 을 통한 기록이 아니므로 어떤
> 이벤트에 의한 어떤 정보의 변경이 일어났음을 *기계적으로* 작성. P4 환경이
> 갖춰져 있을 때만 이 동작이 일어나야 하며 갖춰져있지 않다면 데이터베이스
> 파일에 기록하고 *아무 에러메시지도 보여주지 않아야* 합니다.

설계:
- mutation 직후 `submit_db_to_p4(description)` 호출 — `threading.Thread`
  로 fire-and-forget. UI 스레드 차단 X.
- worker 스레드 안에서:
  1. `p4 info -s` 로 환경 감지 — 비-zero 면 silent return.
  2. db 파일이 P4 workspace 에 매핑돼 있는지 `p4 fstat <db>` 로 확인.
     매핑 안 되면 silent return (사용자 표현: "데이터베이스 파일에 기록
     하고 아무 에러메시지도 보여주지 않아야").
  3. `p4 add` (없으면) / `p4 edit` (있으면) — `p4 reconcile -e` 로 한 번에.
  4. `p4 submit -d <mechanical_description>`.
  5. 어떤 단계든 실패해도 로그만 (`logger.debug` / `logger.warning`) —
     사용자에게 표면화 X.

description 형식 (mechanical, LLM 미관여):
  ``[whooing-tui] entry <id> <action>: <details>``
  예:
    [whooing-tui] entry e1234 memo upsert
    [whooing-tui] entry e1234 hashtags set [식비, 커피]
    [whooing-tui] entry e1234 deleted
    [whooing-tui] entry e1234 attachment add: invoice.pdf (12345 bytes, sha256=ab12cd34)
    [whooing-tui] entry e1234 attachment delete: invoice.pdf

호출자 (entries.py 의 `_persist_local` / `_purge_local`, attachment_browser.py
의 `add_attachment` / `remove`) 가 description 을 직접 만든다 — 본 모듈은
*전송* 만 책임지고 의미적 해석은 안 한다.

CL #51124+ 다중 파일 지원: 첨부 파일을 추가/삭제하면 db 와 파일이 같은
CL 로 묶여 submit 돼야 한다 (사용자 요청: "첨부파일을 서브밋 할 때
데이터베이스, 파일을 함께 서브밋"). `submit_files_to_p4(paths, desc)` 가
공개 API. `submit_db_to_p4` 는 1-원소 리스트 wrapper.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


# 외부 환경에서도 override 가능 — 테스트 친화. 평소에는 PATH 의 p4 를 찾는다.
_P4_BIN_ENV = "WHOOING_P4_BIN"


def _p4_bin() -> str | None:
    """`p4` 실행 경로 — 환경변수 override 우선, 없으면 `shutil.which("p4")`.

    None 이면 P4 환경 없음 → caller 가 silent return.
    """
    explicit = os.getenv(_P4_BIN_ENV)
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.exists() else None
    return shutil.which("p4")


def _run_p4(
    bin_path: str, args: Iterable[str], *, cwd: str | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """p4 subprocess 실행 — UTF-8 텍스트 모드, stdout/stderr 캡처.

    timeout 은 보수적으로 30초 (대부분 0.x 초). 실패 (non-zero exit) 도
    예외 X — caller 가 returncode 검사.
    """
    return subprocess.run(
        [bin_path, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def is_p4_available() -> bool:
    """P4 환경이 갖춰져 있는지 — `p4 info -s` 가 0 return 이면 True.

    `p4` 바이너리 부재 / 클라이언트 미설정 / 서버 통신 실패 모두 False.
    """
    bin_path = _p4_bin()
    if bin_path is None:
        return False
    try:
        proc = _run_p4(bin_path, ["info", "-s"], timeout=5)
    except Exception:
        return False
    return proc.returncode == 0


def is_file_in_p4(path: Path) -> bool:
    """`path` 가 P4 workspace 에 매핑돼 있는지 — `p4 fstat <path>` 검사.

    `p4 fstat` 은 unmapped path 면 stderr 에 메시지 + non-zero. mapped
    이지만 add 안 된 (untracked) 파일도 stderr 메시지 + non-zero. 둘 다
    False — caller 는 "P4 환경에 등록된 db" 일 때만 자동 submit.

    매핑은 됐지만 처음 보는 파일이면 `add` 가 필요한 상태라 별도로 처리:
    `p4 where` 로 매핑 여부만 확인하고 add 는 reconcile 로.
    """
    bin_path = _p4_bin()
    if bin_path is None:
        return False
    try:
        proc = _run_p4(bin_path, ["where", str(path)], timeout=5)
    except Exception:
        return False
    return proc.returncode == 0


def _do_submit_multi(paths: list[Path], description: str) -> str:
    """본 함수는 worker thread 에서 실행. 모든 실패는 로그만.

    CL #51124+: 여러 파일을 한 번의 reconcile + submit 으로 묶는다.
    CL #51136+ (A4): return 값으로 status string — caller 가 사용자 알림.

    return 값:
      'ok'         — submit 성공.
      'no-changes' — reconcile 결과 변경 없음 (또는 'no files to submit').
      'no-p4'      — p4 바이너리 없음 / 환경 부재.
      'unmapped'   — 매핑된 path 0개.
      'noop'       — paths 가 빈 list.
      'error'      — reconcile / submit 실패.
    """
    if not paths:
        log.debug("_do_submit_multi: paths 비어있음 — skip")
        return "noop"
    bin_path = _p4_bin()
    if bin_path is None:
        log.debug("p4 bin 부재 — submit skip")
        return "no-p4"
    cwd = str(paths[0].parent)

    # 매핑 확인.
    mapped: list[str] = []
    for p in paths:
        where = _run_p4(bin_path, ["where", str(p)], cwd=cwd, timeout=5)
        if where.returncode == 0:
            mapped.append(str(p))
        else:
            log.debug("path 가 P4 workspace 매핑 외 — skip: %s", p)
    if not mapped:
        log.debug("매핑된 path 0개 — silent skip")
        return "unmapped"

    # reconcile.
    rec = _run_p4(
        bin_path,
        ["reconcile", "-e", "-a", "-d", *mapped],
        cwd=cwd,
        timeout=10,
    )
    if rec.returncode not in (0, 1):
        log.warning(
            "p4 reconcile failed (rc=%d): %s",
            rec.returncode, (rec.stderr or "").strip(),
        )
        return "error"

    # submit.
    sub = _run_p4(
        bin_path,
        ["submit", "-d", description, *mapped],
        cwd=cwd,
        timeout=30,
    )
    if sub.returncode != 0:
        msg = (sub.stderr or "").strip()
        if "no files to submit" in msg.lower() or "nothing to submit" in msg.lower():
            log.debug("p4 submit: 변경 없음 — skip")
            return "no-changes"
        log.warning("p4 submit failed (rc=%d): %s", sub.returncode, msg)
        return "error"
    log.info("p4 submit 완료 (%d파일): %s", len(mapped), description)
    return "ok"


def _do_submit(db_path: Path, description: str) -> str:
    """후방 호환 — 단일 파일 wrapper. CL #51107 이후의 기존 호출자 (entries
    의 `_persist_local`/`_purge_local`) 가 그대로 사용. 신규 호출자는
    `submit_files_to_p4(paths, ...)` 권장. CL #51136+: status 반환."""
    return _do_submit_multi([db_path], description)


# 활성/대기 중인 submit 스레드. App 종료 직전 `wait_for_pending()` 이
# 모두 join — daemon=True 면 main thread 종료와 함께 죽어 submit 이 미완료로
# 끝나는 회귀 (CL #51118 사용자 보고: 로컬 db 변경이 P4 에 반영 안 됨).
_PENDING: list[threading.Thread] = []
_PENDING_LOCK = threading.Lock()


def submit_files_to_p4(
    paths: list[Path],
    description: str,
    *,
    blocking: bool = False,
    on_complete: Any = None,
) -> None:
    """여러 파일을 한 CL 로 묶어 P4 에 자동 submit — fire-and-forget.

    CL #51124+. 사용자 요청: "첨부파일을 서브밋 할 때 데이터베이스, 파일을
    함께 서브밋하고 디스크립션에 어떤 입력에 의한 서브밋인지 상세히 기입."
    db 와 첨부 파일을 동일 description / 동일 changelist 로 묶는 진입점.

    `description` 은 caller 가 만드는 mechanical 문자열 (LLM 미관여).
    `blocking=True` 는 테스트 전용 — worker 스레드 없이 즉시 실행.

    CL #51118 도입 이후의 thread 추적 / `wait_for_pending` 정책 그대로 적용.

    CL #51136+ (A4): `on_complete(status: str)` callback — submit 끝나면
    호출. status:
      - "ok": p4 환경 + 매핑 OK + submit 성공.
      - "no-changes": 변경 없음 (이미 동일).
      - "no-p4": p4 환경 부재 (silent 정책 그대로 — caller 만 인지).
      - "unmapped": workspace 매핑 외.
      - "error": 그 외 실패.
    """
    if blocking:
        status = "error"
        try:
            status = _do_submit_multi(list(paths), description)
        except Exception:  # pragma: no cover — 절대 예외 표면화 X
            log.exception("p4 submit_files blocking failed")
        if on_complete is not None:
            try:
                on_complete(status)
            except Exception:  # pragma: no cover
                log.exception("on_complete callback failed")
        return

    paths_snapshot = list(paths)

    def _runner() -> None:
        status = "error"
        try:
            status = _do_submit_multi(paths_snapshot, description)
        except Exception:  # pragma: no cover
            log.exception("p4 submit_files thread failed")
        finally:
            with _PENDING_LOCK:
                try:
                    _PENDING.remove(threading.current_thread())
                except ValueError:  # pragma: no cover
                    pass
            if on_complete is not None:
                try:
                    on_complete(status)
                except Exception:  # pragma: no cover
                    log.exception("on_complete callback failed")

    t = threading.Thread(
        target=_runner, name="whooing-p4-sync", daemon=False,
    )
    with _PENDING_LOCK:
        _PENDING.append(t)
    t.start()


def submit_db_to_p4(
    db_path: Path,
    description: str,
    *,
    blocking: bool = False,
) -> None:
    """단일 db 파일 submit — `submit_files_to_p4([db_path], ...)` 의 후방
    호환 wrapper. CL #51107 이후의 호출자 (entries 의 `_persist_local`/
    `_purge_local`) 가 그대로 사용."""
    submit_files_to_p4([db_path], description, blocking=blocking)


def sync_db_from_p4(db_path: Path) -> None:
    """앱 시작 시 호출. P4 환경 + 워크스페이스 매핑 양쪽 OK 면 `p4 sync`
    로 db 파일을 최신 (head) 으로 갱신.

    CL #51119+ 사용자 요청: 다른 환경 (다른 머신 / CL) 에서 submit 된
    db 변경분을 받아오기 위해 매 시작 시점에 동기화.

    실패는 fatal 이 아니다 — `_p4_bin()` 부재 / 매핑 외 / sync 실패 모두
    silent return (사용자 표면화 X). 호출자는 그 다음에 `init_schema()`
    같은 idempotent 작업을 그대로 진행.
    """
    bin_path = _p4_bin()
    if bin_path is None:
        log.debug("p4 bin 부재 — sync skip")
        return
    cwd = str(db_path.parent)
    where = _run_p4(bin_path, ["where", str(db_path)], cwd=cwd, timeout=5)
    if where.returncode != 0:
        log.debug("db 가 P4 workspace 매핑 외 — sync skip")
        return
    sync = _run_p4(bin_path, ["sync", str(db_path)], cwd=cwd, timeout=30)
    if sync.returncode != 0:
        # `file(s) up-to-date` 도 stderr 로 나오는 케이스가 있어 무해 — 디버그 만.
        log.debug(
            "p4 sync (rc=%d): %s",
            sync.returncode, (sync.stderr or sync.stdout or "").strip(),
        )
    else:
        log.info("p4 sync 완료: %s", db_path)


def flush_on_exit(
    db_path: Path,
    *,
    description: str = "[whooing-tui] session end — flush pending db changes",
) -> None:
    """앱 종료 직전에 호출. 다음 두 가지를 순차 수행:

    1. `wait_for_pending()` — 진행 중이던 submit worker 들 join.
    2. *추가 안전망*: 그 사이에도 미반영 로컬 변경이 있을 수 있으므로
       blocking 으로 한 번 더 `_do_submit` (변경 없으면 `p4 submit` 의
       'no files to submit' 으로 silent skip — `_do_submit` 가 처리).

    사용자 요청 (CL #51119+): "종료할 때, 데이터를 변경할 때마다 서브밋."
    매 mutation 의 자동 submit 은 이미 `_persist_local`/`_purge_local`
    경로에 있고, 본 함수는 *마지막 안전망* — race / 누락 케이스 보호.
    """
    wait_for_pending()
    try:
        _do_submit(db_path, description)
    except Exception:  # pragma: no cover
        log.debug("flush_on_exit submit failed (silent)", exc_info=True)


def wait_for_pending(timeout_per_thread: float = 30.0) -> None:
    """모든 활성 submit 스레드를 join — App 종료 직전에 호출.

    각 스레드 마다 `timeout_per_thread` 초 대기. 그 안에 안 끝나면 포기
    (사용자 종료 흐름을 무한 차단하지 않게). 타임아웃은 `_do_submit`
    내부의 `p4 submit -d ...` 의 30s 타임아웃과 동일.
    """
    with _PENDING_LOCK:
        snapshot = list(_PENDING)
    for t in snapshot:
        try:
            t.join(timeout=timeout_per_thread)
        except Exception:  # pragma: no cover
            log.exception("p4 sync join failed")


# ---- mechanical description builder ----------------------------------


def describe_annotation(
    *, entry_id: str, memo_changed: bool, tags: list[str] | None,
    deleted: bool = False,
    previous_tags: list[str] | None = None,
) -> str:
    """mutation 의 시각/내용을 *LLM 없이* 기계적으로 한 줄 description.

    CL #51141+ (H10): `previous_tags` 가 명시되면 added/removed diff 도 표시 —
    P4 history 에서 "어떤 태그가 추가됐고 사라졌는지" 한 줄에 보임.

    사용 예:
      describe_annotation(entry_id="e123", memo_changed=True, tags=None)
        → "[whooing-tui] entry e123 memo upsert"
      describe_annotation(entry_id="e123", memo_changed=False, tags=["식비"])
        → "[whooing-tui] entry e123 hashtags set [식비]"
      describe_annotation(entry_id="e123", memo_changed=False, tags=["식비", "외식"],
                          previous_tags=["식비"])
        → "[whooing-tui] entry e123 hashtags set [식비, 외식] (+외식)"
      describe_annotation(entry_id="e123", memo_changed=False, tags=["외식"],
                          previous_tags=["식비", "외식"])
        → "[whooing-tui] entry e123 hashtags set [외식] (-식비)"
      describe_annotation(entry_id="e123", deleted=True)
        → "[whooing-tui] entry e123 deleted"
    """
    parts: list[str] = []
    if deleted:
        parts.append("deleted")
    else:
        if memo_changed:
            parts.append("memo upsert")
        if tags is not None:
            tag_repr = "[" + ", ".join(tags) + "]"
            tag_part = f"hashtags set {tag_repr}"
            if previous_tags is not None:
                added = [t for t in tags if t not in previous_tags]
                removed = [t for t in previous_tags if t not in tags]
                diff_bits: list[str] = []
                if added:
                    diff_bits.append("+" + "+".join(added))
                if removed:
                    diff_bits.append("-" + "-".join(removed))
                if diff_bits:
                    tag_part += f" ({' '.join(diff_bits)})"
            parts.append(tag_part)
    if not parts:
        parts.append("noop")
    return f"[whooing-tui] entry {entry_id} " + "; ".join(parts)


def describe_attachment_add(
    *,
    entry_id: str,
    filename: str,
    size_bytes: int | None,
    sha256: str | None,
) -> str:
    """첨부 추가 — db row insert + 파일 디스크 복사 직후 호출.

    CL #51124+. LLM 미관여, 어떤 사용자 입력이 어떤 파일을 추가했는지 한
    줄에 명시 (사용자 요청: "디스크립션에 어떤 입력에 의한 서브밋인지
    상세히 기입").

    형식:
      [whooing-tui] entry <id> attachment add: <filename> (<size> bytes, sha256=<8chars>)

    예:
      describe_attachment_add(entry_id="e123", filename="invoice.pdf",
                              size_bytes=12345, sha256="ab12cd34ef56...")
        → "[whooing-tui] entry e123 attachment add: invoice.pdf (12345 bytes, sha256=ab12cd34)"

    size_bytes 가 None 이면 size 부분 생략, sha256 가 None 이면 sha256 부분
    생략 (defensive — caller 가 stat 못 한 케이스).
    """
    meta_parts: list[str] = []
    if size_bytes is not None:
        meta_parts.append(f"{size_bytes} bytes")
    if sha256:
        meta_parts.append(f"sha256={sha256[:8]}")
    meta = f" ({', '.join(meta_parts)})" if meta_parts else ""
    return f"[whooing-tui] entry {entry_id} attachment add: {filename}{meta}"


def describe_attachment_delete(
    *,
    entry_id: str,
    filename: str,
    kept_other_refs: int = 0,
) -> str:
    """첨부 삭제 — db row 제거 + (옵션) 파일 디스크 unlink 직후 호출.

    CL #51124+. dedup 보존 케이스 (`kept_other_refs > 0`) 는 디스크 파일이
    살아있고 db row 만 사라진 상태 — description 에 명시해 사용자가 P4 log
    에서 "왜 파일이 안 사라졌는지" 구분 가능.

    형식:
      [whooing-tui] entry <id> attachment delete: <filename>
      [whooing-tui] entry <id> attachment delete (db only, file kept — N other refs): <filename>

    """
    if kept_other_refs > 0:
        return (
            f"[whooing-tui] entry {entry_id} attachment delete "
            f"(db only, file kept — {kept_other_refs} other refs): {filename}"
        )
    return f"[whooing-tui] entry {entry_id} attachment delete: {filename}"
