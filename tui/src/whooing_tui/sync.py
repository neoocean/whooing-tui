"""머신 간 동기화 백엔드 facade — 코어는 백엔드(P4 등)를 직접 모른다.

후잉 API 가 모르는 로컬 데이터(메모/태그/첨부)는 sqlite 에 보관한다. 이를
여러 머신에서 공유하려면 *선택적* 동기화 백엔드가 필요하다. 하지만 Perforce
를 일상에서 쓸 수 있는 사용자는 매우 적으므로 **기본은 'none'(동기화 안 함)**
이다 — P4 를 쓸 수 있는 사용자만 config 또는 환경변수로 켠다.

코어(app / data / repository / revision_repo / screens / cli)는 본 모듈만
호출하고 `p4_sync` 를 직접 import 하지 않는다. 백엔드가 'none' 이면 모든
동작은 no-op, 질의는 안전한 기본값(False / 0)을 돌려준다 — P4 환경이 전혀
없어도 코어는 그대로 동작하고 어떤 P4 호출/알림도 발생하지 않는다.

백엔드 결정 우선순위 (`configure()` 가 앱/CLI 시작 시 한 번 호출):
  1. 환경변수 `WHOOING_SYNC_BACKEND` (`none` | `p4`)
  2. config `[sync] backend`
  3. 기본값 `none`

새 백엔드(예: git)를 붙이려면 본 facade 함수들에 분기를 추가하면 되고,
코어 호출부는 손대지 않는다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NONE = "none"
P4 = "p4"
_VALID = {NONE, P4}

# configure() 로 명시 설정된 활성 백엔드. None 이면 미설정 (env-only 로 해석).
_active: str | None = None


def _normalize(name: str | None) -> str | None:
    """백엔드 이름 정규화 — 유효하지 않으면 'none' 으로 강등(경고).

    빈 값/None → None(미지정). 알 수 없는 값 → NONE(명시적 무효).
    """
    if not name:
        return None
    v = str(name).strip().lower()
    if v in _VALID:
        return v
    log.warning("알 수 없는 sync 백엔드 %r — 'none' 으로 처리", name)
    return NONE


def resolve(config: Any = None) -> str:
    """env > config > 기본('none') 순으로 백엔드 이름을 결정.

    `config` 는 `config.Config` (또는 `sync_backend` 속성을 가진 객체). env
    가 설정돼 있으면 config 보다 우선한다.
    """
    env = _normalize(os.getenv("WHOOING_SYNC_BACKEND"))
    if env is not None:
        return env
    if config is not None:
        cfg_backend = _normalize(getattr(config, "sync_backend", None))
        if cfg_backend is not None:
            return cfg_backend
    return NONE


def configure(backend: str) -> None:
    """활성 백엔드를 명시 설정 — 앱/CLI 시작 시 `resolve(cfg)` 결과로 호출."""
    global _active
    _active = _normalize(backend) or NONE
    log.info("sync backend = %s", _active)


def active_backend() -> str:
    """현재 활성 백엔드. `configure()` 전이면 env-only 로 해석(기본 'none')."""
    if _active is not None:
        return _active
    return resolve()


def is_enabled() -> bool:
    """동기화 백엔드가 활성인지 ('none' 이 아니면 True)."""
    return active_backend() != NONE


def reset() -> None:
    """테스트용 — `configure()` 상태 초기화 (다음 호출은 env-only 해석)."""
    global _active
    _active = None


# ---- 변경 제출 (mutation 시점) --------------------------------------------

def submit_db(db_path: Path, description: str, *, blocking: bool = False) -> None:
    """단일 db 파일 변경을 동기화 백엔드에 제출(enqueue). 'none' 이면 no-op."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        p4_sync.submit_db_to_p4(db_path, description, blocking=blocking)


def submit_files(
    paths: list[Path],
    description: str,
    *,
    blocking: bool = False,
    on_complete: Any = None,
) -> None:
    """여러 파일(db + 첨부 등) 변경을 한 단위로 제출. 'none' 이면 no-op.

    백엔드가 없으면 `on_complete` 도 호출하지 않는다 — 비-P4 사용자에게
    동기화 관련 알림이 전혀 보이지 않도록(완전 무시).
    """
    if active_backend() == P4:
        from whooing_tui import p4_sync
        p4_sync.submit_files_to_p4(
            list(paths), description,
            blocking=blocking, on_complete=on_complete,
        )


# ---- 시작/종료 동기화 ------------------------------------------------------

def sync_on_startup(db_path: Path) -> None:
    """앱 시작 시 다른 머신의 변경을 받아온다(p4 sync 등). 'none' 이면 no-op."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        p4_sync.sync_db_from_p4(db_path)


def startup_has_pending(db_path: Path) -> bool:
    """로컬에 미제출 변경이 있는지. 백엔드 없으면 False."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        return p4_sync.has_pending_local_changes(db_path)
    return False


def startup_is_outdated(db_path: Path) -> bool:
    """로컬 db 가 원격 head 보다 오래됐는지. 백엔드 없으면 False."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        return p4_sync.is_outdated_vs_p4(db_path)
    return False


def mark_session_mutated() -> None:
    """세션이 db 변경을 만들었음을 표시(flush short-circuit 우회). no-op if none."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        p4_sync.mark_session_mutated()


def flush_on_exit(db_path: Path, *, description: str | None = None) -> None:
    """종료 직전 누적 변경을 한 번에 제출. 백엔드 없으면 no-op."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        p4_sync.flush_on_exit(db_path, description=description)


def wait_for_pending(timeout_per_thread: float = 30.0) -> None:
    """진행 중인 제출 스레드들을 join. 백엔드 없으면 no-op."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        p4_sync.wait_for_pending(timeout_per_thread)


def pending_count() -> int:
    """진행 중인 제출 작업 수(종료 모달 라이브 표시용). 백엔드 없으면 0."""
    if active_backend() == P4:
        from whooing_tui import p4_sync
        return p4_sync.pending_count()
    return 0


# ---- 변경 설명 빌더 (백엔드 중립, 순수 함수) --------------------------------
# 동기화 백엔드의 commit/CL description 으로 쓰이는 기계적 설명. 백엔드가
# 꺼져 있어도 호출자가 안전하게 만들 수 있도록 그대로 위임한다(미사용이면
# 버려질 뿐, 부작용 없음). 구현은 현재 P4 백엔드(p4_sync)에 둔다.

def describe_annotation(**kwargs: Any) -> str:
    from whooing_tui import p4_sync
    return p4_sync.describe_annotation(**kwargs)


def describe_revision(**kwargs: Any) -> str:
    from whooing_tui import p4_sync
    return p4_sync.describe_revision(**kwargs)


def describe_attachment_add(**kwargs: Any) -> str:
    from whooing_tui import p4_sync
    return p4_sync.describe_attachment_add(**kwargs)


def describe_attachment_delete(**kwargs: Any) -> str:
    from whooing_tui import p4_sync
    return p4_sync.describe_attachment_delete(**kwargs)
