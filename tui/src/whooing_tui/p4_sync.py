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


def has_pending_local_changes(path: Path) -> bool:
    """CL #52832+: `path` 가 로컬에서 수정되어 아직 P4 submit 전인지.

    `p4 reconcile -n -m <path>` 의 출력으로 판단 — `-n` 은 preview 라 실제
    open 하지 않고, `-m` 은 디스크 modtime + size + checksum 으로 변경 감지.

    `True`: 로컬에 unsubmitted 변경 있음 — caller 는 `flush_on_exit` 같은
            blocking submit 후 진행해야.
    `False`: 변경 없음 / P4 환경 부재 / 매핑 외 (caller 가 silent 처리).
    """
    bin_path = _p4_bin()
    if bin_path is None:
        return False
    if not is_file_in_p4(path):
        return False
    try:
        proc = _run_p4(
            bin_path, ["reconcile", "-n", "-m", str(path)],
            cwd=str(path.parent), timeout=10,
        )
    except Exception:
        log.debug("p4 reconcile -n failed", exc_info=True)
        return False
    # reconcile -n 의 stdout 에 변경 후보가 한 줄씩. 없으면 stdout 빈문자열
    # + stderr 에 "no file(s) to reconcile". rc 는 변경 있을 때 0, 없을 때
    # 1 인 경우가 많지만 환경마다 차이 — stdout 내용으로 판단.
    out = (proc.stdout or "").strip()
    return bool(out)


def is_outdated_vs_p4(path: Path) -> bool:
    """CL #52832+: `path` 가 P4 head 보다 *오래된* (sync 필요한) 상태인지.

    `p4 sync -n <path>` 의 출력으로 판단:
      - "file(s) up-to-date" → False (최신).
      - 파일이 새 rev 로 업데이트되거나 추가될 거라는 메시지 → True.
      - P4 환경 부재 / 매핑 외 → False (caller 가 strict 모드 X).

    caller 는 True 면 사용자에게 "DB 최신 아님 — p4 sync 후 재시작" 안내.
    """
    bin_path = _p4_bin()
    if bin_path is None:
        return False
    if not is_file_in_p4(path):
        return False
    try:
        proc = _run_p4(
            bin_path, ["sync", "-n", str(path)],
            cwd=str(path.parent), timeout=10,
        )
    except Exception:
        log.debug("p4 sync -n failed", exc_info=True)
        return False
    combined = (proc.stdout or "") + (proc.stderr or "")
    # "up-to-date" 가 어디든 (stdout 또는 stderr) 들어 있으면 최신.
    if "up-to-date" in combined:
        return False
    # stdout 에 sync 후보가 있다는 메시지 ("#N - updating", "#N - added as")
    # 있으면 outdated. 빈 stdout 이면 (예: invalid path) False 로 보수.
    return bool((proc.stdout or "").strip())


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


def _create_numbered_change(
    bin_path: str, description: str, cwd: str,
) -> str | None:
    """`p4 change -i` 로 numbered CL 을 만들고 CL 번호 반환. 실패 시 None.

    CL #52749+: 사용자 워크스페이스 규칙 (MEMORY.md §5.1 default CL 금지)
    + `p4 submit -d <desc> <local-path>` 가 P4 syntax 오류라는 사실
    (path 인자는 depot/client syntax 만 허용) 을 동시에 해결.

    `description` 의 각 라인은 spec format 대로 \\t 들여쓰기 필요.
    """
    spec_lines = ["Change: new", f"Client: {os.environ.get('P4CLIENT', '')}",
                  "Status: new", "Description:"]
    for line in description.splitlines() or [""]:
        spec_lines.append(f"\t{line}" if line else "\t")
    spec = "\n".join(spec_lines) + "\n"
    proc = subprocess.run(
        [bin_path, "change", "-i"],
        input=spec, cwd=cwd, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=10, check=False,
    )
    if proc.returncode != 0:
        log.warning(
            "p4 change -i failed (rc=%d): %s",
            proc.returncode, (proc.stderr or "").strip(),
        )
        return None
    # 출력: "Change NNNNN created."
    for tok in (proc.stdout or "").split():
        if tok.isdigit():
            return tok
    log.warning("p4 change -i: CL 번호 parse 실패 — stdout=%r", proc.stdout)
    return None


def _do_submit_multi(paths: list[Path], description: str) -> str:
    """본 함수는 worker thread 에서 실행. 모든 실패는 로그만.

    CL #51124+: 여러 파일을 한 번의 reconcile + submit 으로 묶는다.
    CL #51136+ (A4): return 값으로 status string — caller 가 사용자 알림.
    CL #52749+: numbered CL 패턴 채택 (사용자 보고: submit 실패).
      종전: reconcile → `p4 submit -d <desc> <local-paths>`. 이 호출은
            P4 의 submit 문법 오류 (`Usage: submit ... [file]`) — file
            arg 는 절대 local path 가 아닌 depot/client syntax 만.
            결과로 모든 첨부 submit 이 fail status 반환.
      신규: change 먼저 만들고 → reconcile -c <CL> → submit -c <CL>.
            이 패턴은 우리 MEMORY.md §5.1 "default CL 금지" 정책과도
            일치하고 다른 client 의 동시 작업과 격리.

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

    # numbered CL 생성. 실패하면 submit 단계도 skip.
    cl = _create_numbered_change(bin_path, description, cwd=cwd)
    if cl is None:
        return "error"

    # reconcile — 그 CL 로 directly open. -e edit / -a add / -d delete.
    rec = _run_p4(
        bin_path,
        ["reconcile", "-c", cl, "-e", "-a", "-d", *mapped],
        cwd=cwd,
        timeout=10,
    )
    if rec.returncode not in (0, 1):
        log.warning(
            "p4 reconcile failed (rc=%d): %s",
            rec.returncode, (rec.stderr or "").strip(),
        )
        # 빈 CL 정리 시도 (best-effort) — 파일 안 들어간 CL 은 무가치.
        _run_p4(bin_path, ["change", "-d", cl], cwd=cwd, timeout=5)
        return "error"

    # CL 에 실제로 열린 파일이 있는지 확인 — 변경이 아예 없으면 빈 CL 삭제 후 no-changes.
    opened = _run_p4(bin_path, ["opened", "-c", cl], cwd=cwd, timeout=5)
    if (opened.stdout or "").strip() == "":
        log.debug("CL %s 에 opened 파일 0 — 변경 없음, 빈 CL 삭제", cl)
        _run_p4(bin_path, ["change", "-d", cl], cwd=cwd, timeout=5)
        return "no-changes"

    # submit — `-c <CL>` 로 정확히 그 CL 만.
    sub = _run_p4(
        bin_path,
        ["submit", "-c", cl],
        cwd=cwd,
        timeout=30,
    )
    if sub.returncode != 0:
        msg = (sub.stderr or "").strip()
        if "no files to submit" in msg.lower() or "nothing to submit" in msg.lower():
            log.debug("p4 submit: 변경 없음 — skip")
            # 빈 CL 정리.
            _run_p4(bin_path, ["change", "-d", cl], cwd=cwd, timeout=5)
            return "no-changes"
        log.warning("p4 submit failed (rc=%d): %s", sub.returncode, msg)
        return "error"
    log.info("p4 submit 완료 (CL %s, %d파일): %s", cl, len(mapped), description)
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


# CL #52853+: 세션 동안 한 번이라도 db 변경 submit 시도가 있었는지 추적.
# `flush_on_exit` 가 *안전망* `_do_submit` 을 건너뛸지 결정 — 사용자 요청:
# "아무것도 편집하지 않았다면 서브밋 하지 않을 수 있습니까?"
#
# 종전엔 종료 때마다 `p4 reconcile -n` 라운드트립이 발생해 변경 없음을
# 확인하고 빈 CL 을 삭제 (no-op 이지만 네트워크 비용 + p4 server 부담).
# 본 플래그가 False 면 그 라운드트립 자체를 생략.
#
# 플래그는 *프로세스 수명 동안* 유효 — `submit_files_to_p4` /
# `submit_db_to_p4` 호출 시점에 True. 시작 시 `has_pending_local_changes`
# 가 True 면 caller (`_StartupCheckScreen`) 가 `mark_session_mutated()` 를
# 명시 호출해 startup-time flush 가 정상 동작하도록.
_SESSION_MUTATED: bool = False


def mark_session_mutated() -> None:
    """본 프로세스가 db 변경을 만들었거나 처리해야 함을 표시.

    `submit_files_to_p4` 자동 호출. caller 가 *기존* dirty 상태 (다른
    프로세스가 남긴 변경 등) 를 처리하기 전에도 명시 호출 가능.
    """
    global _SESSION_MUTATED
    _SESSION_MUTATED = True


def is_session_mutated() -> bool:
    """현 프로세스가 세션 시작 이후 db 변경 submit 을 시도한 적 있는지."""
    return _SESSION_MUTATED


def reset_session_mutated() -> None:
    """테스트 격리용 — 일반 코드는 호출하지 않는다."""
    global _SESSION_MUTATED
    _SESSION_MUTATED = False


# ----------------------------------------------------------------------
# 세션 변경 journal (CL #53093+).
# ----------------------------------------------------------------------
#
# 사용자 요청 (2026-05-19): "지금은 매 수정마다 데이터베이스를 서브밋하고
# 있는데 이를 앱 시작할 때 최신화, 앱 종료할때 서브밋하도록 변경해주세요.
# 단 앱 종료할때 서브밋할때는 디스크립션에 이 서브밋에 어떤 수정들이
# 포함되어있는지 디스크립션에 잘 작성해주세요."
#
# 종전 정책: 매 mutation 마다 `submit_files_to_p4` 가 daemon=False 스레드
# spawn → 즉시 `p4 submit` 실행. 결과: 5분 작업에 CL 수십 개, 각각 한 줄
# description.
#
# 새 정책 (CL #53093+):
#   1. 매 mutation 의 `submit_*` 호출 → `_CHANGE_JOURNAL` 에 enqueue (즉시
#      반환, p4 호출 없음).
#   2. 앱 종료 시 `flush_on_exit` 가 journal 을 한 CL 로 묶어 `_do_submit_
#      multi(all_paths, aggregated_description)` 단일 호출.
#   3. aggregated description = journal 의 description 들을 그룹화 + 카운트
#      해 multi-line 으로 build (`_build_aggregated_description`).
_CHANGE_JOURNAL: list[tuple[list[Path], str]] = []
_JOURNAL_LOCK = threading.Lock()


def enqueue_db_change(paths: list[Path], description: str) -> None:
    """세션 변경을 journal 에 적재. `flush_on_exit` 가 한 CL 로 묶어 submit.

    호출자 (예: EntryRepository / entries.py) 의 기존 `submit_db_to_p4` /
    `submit_files_to_p4` 호출이 본 함수로 위임된다 — API 그대로 사용.

    `mark_session_mutated()` 도 자동 호출 — flush_on_exit 의 dirty 게이트
    통과.
    """
    paths_snap = [Path(p) for p in paths]
    with _JOURNAL_LOCK:
        _CHANGE_JOURNAL.append((paths_snap, str(description)))
    mark_session_mutated()


def get_journal_snapshot() -> list[tuple[list[Path], str]]:
    """현재 journal 의 (paths, description) tuple list 복사본."""
    with _JOURNAL_LOCK:
        return [(list(p), d) for p, d in _CHANGE_JOURNAL]


def clear_journal() -> None:
    """journal 비움 — flush 직후 / 테스트 격리에서 호출."""
    with _JOURNAL_LOCK:
        _CHANGE_JOURNAL.clear()


def journal_size() -> int:
    """journal 에 쌓인 변경 수 — shutdown modal 의 라이브 카운트 등."""
    with _JOURNAL_LOCK:
        return len(_CHANGE_JOURNAL)


def _build_aggregated_description(
    entries: list[tuple[list[Path], str]],
    *,
    header: str | None = None,
) -> str:
    """journal 을 multi-line description 으로 빌드.

    같은 description 이 반복되면 묶고 카운트 표기 — "(3x) entry e123
    deleted" 형태. caller 의 mechanical 문자열을 보존해 사람이 읽기 좋게.

    output 예:
        [whooing-tui] session end — 5 db changes bundled

          · (3x) entry 1713038 deleted
          · annotation persist e456 — memo + tags
          · batch tag add #식비: 4/4 entries
    """
    n = len(entries)
    if n == 0:
        return header or "[whooing-tui] session end — no changes"

    counts: dict[str, int] = {}
    order: list[str] = []
    for _paths, desc in entries:
        if desc not in counts:
            counts[desc] = 0
            order.append(desc)
        counts[desc] += 1

    plural = "s" if n != 1 else ""
    head = header or (
        f"[whooing-tui] session end — {n} db change{plural} bundled"
    )
    lines: list[str] = [head, ""]
    for desc in order:
        # 호출자가 "[whooing-tui] foo" prefix 를 붙였으면 header 중복이라 제거.
        clean = desc
        if clean.startswith("[whooing-tui] "):
            clean = clean[len("[whooing-tui] "):]
        c = counts[desc]
        if c == 1:
            lines.append(f"  · {clean}")
        else:
            lines.append(f"  · ({c}x) {clean}")
    return "\n".join(lines)


def submit_files_to_p4(
    paths: list[Path],
    description: str,
    *,
    blocking: bool = False,
    on_complete: Any = None,
) -> None:
    """여러 파일의 변경을 journal 에 enqueue — 종료 시 한 CL 로 묶음 submit.

    CL #51124+ 도입, CL #53093+ 정책 변경. 사용자 요청 (2026-05-19):
    "매 수정마다 서브밋" → "시작 시 sync + 종료 시 한 번 submit + aggregated
    description".

    매 호출은 *즉시* 반환 — p4 호출 없음. 실제 submit 은 `flush_on_exit`
    에서.

    파라미터:
      paths, description — journal 의 한 entry 가 됨.
      blocking=True — 테스트 / emergency-only path. journal 우회하고 즉시
        `_do_submit_multi` 실행. on_complete 도 그 결과 status 로 호출.
      on_complete — blocking=True 면 submit 결과 status, blocking=False
        면 즉시 "queued" 로 호출 (호환성).
    """
    if blocking:
        # 테스트 / emergency: journal 우회.
        mark_session_mutated()
        status = "error"
        try:
            status = _do_submit_multi(list(paths), description)
        except Exception:  # pragma: no cover
            log.exception("p4 submit_files blocking failed")
        if on_complete is not None:
            try:
                on_complete(status)
            except Exception:  # pragma: no cover
                log.exception("on_complete callback failed")
        return
    # 일반 path — enqueue only.
    enqueue_db_change(paths, description)
    if on_complete is not None:
        try:
            on_complete("queued")
        except Exception:  # pragma: no cover
            log.exception("on_complete callback failed")


def submit_db_to_p4(
    db_path: Path,
    description: str,
    *,
    blocking: bool = False,
) -> None:
    """단일 db 파일 변경 enqueue. CL #53093+ 부터 즉시 submit 안 함 (journal
    경유). `submit_files_to_p4([db_path], ...)` 의 후방 호환 wrapper."""
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
    description: str | None = None,
) -> None:
    """앱 종료 직전 호출. journal 의 모든 변경을 한 CL 로 묶어 submit.

    CL #53093+ 정책 (사용자 요청): 매 mutation 즉시 submit 대신 종료
    시점에 한 번 묶음 submit. journal 의 description 들을 그룹화 +
    카운트해 aggregated multi-line description 작성.

    `description` 인자:
      None (default) → "[whooing-tui] session end — N db changes bundled"
      특정 string → header 로 사용 (테스트 / 명시 호출 케이스).

    journal 이 비어있고 mutation 도 없었으면 noop. 비어있어도 `_SESSION_
    MUTATED` 가 True 면 (예: 시작 시 has_pending_local_changes 보고 caller
    가 `mark_session_mutated()` 명시 호출) db_path 1개로 안전망 submit —
    다른 프로세스가 남긴 변경 처리.

    CL #51118+ 호환: legacy `submit_files_to_p4(blocking=True)` 또는 미래의
    다른 thread spawn 경로가 남아있을 수 있으므로 먼저 `wait_for_pending()`.
    """
    wait_for_pending()
    if not _SESSION_MUTATED:
        log.debug(
            "flush_on_exit: 세션 동안 mutation 없음 — 안전망 submit 생략",
        )
        return

    journal = get_journal_snapshot()
    clear_journal()

    # journal 에서 paths 수집 (dedup). 비어있어도 db_path 만으로 한 번 시도
    # (시작 시 has_pending_local_changes 가 True 였던 케이스 보호).
    all_paths: list[Path] = []
    seen: set[str] = set()
    for paths, _desc in journal:
        for p in paths:
            sp = str(p)
            if sp not in seen:
                seen.add(sp)
                all_paths.append(p)
    db_str = str(db_path)
    if db_str not in seen:
        all_paths.insert(0, db_path)

    desc = _build_aggregated_description(journal, header=description)
    try:
        _do_submit_multi(all_paths, desc)
    except Exception:  # pragma: no cover
        log.debug("flush_on_exit submit failed (silent)", exc_info=True)


def pending_count() -> int:
    """현재 진행 중인 p4 submit thread 의 개수.

    CL #52819+ — 종료 모달이 "남은 P4 submit N건" 처럼 라이브 표시할 때 사용.
    """
    with _PENDING_LOCK:
        return sum(1 for t in _PENDING if t.is_alive())


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


def describe_revision(
    *, op: str, logical_id: str, entry_id: str | None,
    revision_no: int | None = None, summary: str | None = None,
) -> str:
    """거래 수정 이력(시나리오 11) 기록의 P4 description — LLM 미관여.

    예:
      describe_revision(op="edit", logical_id="1001", entry_id="1001",
                        revision_no=2, summary="money 30,000→27,000")
        → "[whooing-tui] revision edit logical=1001 entry=1001 rev=2 (money 30,000→27,000)"
      describe_revision(op="delete", logical_id="1001", entry_id=None, revision_no=3)
        → "[whooing-tui] revision delete logical=1001 rev=3"
    """
    parts = [f"[whooing-tui] revision {op} logical={logical_id}"]
    if entry_id:
        parts.append(f"entry={entry_id}")
    if revision_no is not None:
        parts.append(f"rev={revision_no}")
    line = " ".join(parts)
    if summary:
        line += f" ({summary})"
    return line


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
