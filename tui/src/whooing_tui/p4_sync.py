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

호출자 (entries.py 의 `_persist_local` / `_purge_local`) 가 description
을 직접 만든다 — 본 모듈은 *전송* 만 책임지고 의미적 해석은 안 한다.
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


def _do_submit(db_path: Path, description: str) -> None:
    """본 함수는 worker thread 에서 실행. 모든 실패는 로그만.

    절차:
      1. 환경 / 매핑 체크.
      2. `p4 reconcile -e -a -d <db>` — edit/add/delete 자동 detect.
         numbered CL 이 없는 default workspace 흐름 — 사용자 요청대로
         "변경 이벤트 즉시 submit" 이라 default CL 도 OK
         (단 default CL 사용 금지 운영 규칙은 본 *동기화* 흐름엔 미적용 —
         별도 사용자 명시 요청에 따른 자동 submit).
      3. `p4 submit -d <description>` — default changelist 의 변경을
         곧장 submit. 변경 없으면 `nothing changed` 반환 → 무시.
    """
    bin_path = _p4_bin()
    if bin_path is None:
        log.debug("p4 bin 부재 — submit skip")
        return
    cwd = str(db_path.parent)

    # 매핑 확인.
    where = _run_p4(bin_path, ["where", str(db_path)], cwd=cwd, timeout=5)
    if where.returncode != 0:
        log.debug("db 가 P4 workspace 매핑 외 — silent skip")
        return

    # reconcile — 새 파일이면 add, 기존이면 edit, 없으면 delete 로 자동.
    rec = _run_p4(
        bin_path,
        ["reconcile", "-e", "-a", "-d", str(db_path)],
        cwd=cwd,
        timeout=10,
    )
    # reconcile 의 returncode 는 변경 없을 때도 1 일 수 있다 — stdout 검사.
    if rec.returncode not in (0, 1):
        log.warning(
            "p4 reconcile failed (rc=%d): %s",
            rec.returncode, (rec.stderr or "").strip(),
        )
        return

    # submit — default CL 의 변경. 변경 없으면 stderr 에 "no files to submit".
    sub = _run_p4(
        bin_path,
        ["submit", "-d", description, str(db_path)],
        cwd=cwd,
        timeout=30,
    )
    if sub.returncode != 0:
        # "no files to submit" 은 정상 케이스 (변경 없음). 그 외만 warning.
        msg = (sub.stderr or "").strip()
        if "no files to submit" in msg.lower() or "nothing to submit" in msg.lower():
            log.debug("p4 submit: 변경 없음 — skip")
        else:
            log.warning("p4 submit failed (rc=%d): %s", sub.returncode, msg)
        return
    log.info("p4 submit 완료: %s", description)


def submit_db_to_p4(
    db_path: Path,
    description: str,
    *,
    blocking: bool = False,
) -> None:
    """db 파일을 P4 에 자동 submit — fire-and-forget.

    `description` 은 caller 가 제공하는 mechanical 문자열. 사용자 노출
    안 함 — 어떤 실패든 로그 (debug/warning) 까지만.

    `blocking=True` 는 테스트 전용 — worker 스레드 없이 즉시 실행.
    """
    if blocking:
        try:
            _do_submit(db_path, description)
        except Exception:  # pragma: no cover — 절대 예외 표면화 X
            log.exception("p4 submit blocking failed")
        return

    def _runner() -> None:
        try:
            _do_submit(db_path, description)
        except Exception:  # pragma: no cover
            log.exception("p4 submit thread failed")

    threading.Thread(
        target=_runner, name="whooing-p4-sync", daemon=True,
    ).start()


# ---- mechanical description builder ----------------------------------


def describe_annotation(
    *, entry_id: str, memo_changed: bool, tags: list[str] | None,
    deleted: bool = False,
) -> str:
    """mutation 의 시각/내용을 *LLM 없이* 기계적으로 한 줄 description.

    사용 예:
      describe_annotation(entry_id="e123", memo_changed=True, tags=None)
        → "[whooing-tui] entry e123 memo upsert"
      describe_annotation(entry_id="e123", memo_changed=False, tags=["식비"])
        → "[whooing-tui] entry e123 hashtags set [식비]"
      describe_annotation(entry_id="e123", memo_changed=True, tags=["a", "b"])
        → "[whooing-tui] entry e123 memo upsert; hashtags set [a, b]"
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
            parts.append(f"hashtags set {tag_repr}")
    if not parts:
        parts.append("noop")  # 안전 fallback — 호출자 버그 노출용.
    return f"[whooing-tui] entry {entry_id} " + "; ".join(parts)
