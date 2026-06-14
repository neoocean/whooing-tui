"""tui 통합 테스트 공통 fixture.

CL #51031+ 부터 EntriesScreen 의 자체 부팅이 `save_last_section_id` 를
호출 — `~/.config/whooing-tui/state.json` 을 건드린다. 실 사용자 home 을
만지지 않도록 모든 테스트에서 `$XDG_CONFIG_HOME` 을 tmp_path 로 격리.

또한 `WHOOING_SECTION_ID` 환경변수 (실 사용자 .env 에서 set 됐을 수 있는
것) 를 delete — 자동 활성화 우선순위 테스트가 환경에 새지 않도록.

CL #51076+: EntriesScreen 의 mutation worker 가 로컬 sqlite (`~/.whooing/
whooing-data.sqlite`) 에 memo + 해시태그를 mirror — 실 사용자 db 를 건드리지
않도록 `WHOOING_DATA_DIR` 도 tmp_path 로 격리.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_user_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("WHOOING_DATA_DIR", str(tmp_path / "whooing"))
    monkeypatch.delenv("WHOOING_SECTION_ID", raising=False)
    # 동기화 백엔드는 테스트에서 항상 'none' (env 가 config 보다 우선) — 개발
    # 머신의 실 `tui/whooing-tui.toml`(backend="p4")이 테스트에 새지 않도록.
    # P4 동작을 검사하는 테스트는 sync.configure("p4") 또는 이 env 를 직접
    # 덮어써 opt-in 한다.
    monkeypatch.setenv("WHOOING_SYNC_BACKEND", "none")
    yield


@pytest.fixture(autouse=True)
def _isolate_p4_pending():
    """CL #51155+ (review C3): p4_sync._PENDING 글로벌 thread list 격리.

    종전엔 한 테스트의 `submit_db_to_p4` 가 thread 를 spawn 하면 다음 테스트
    까지 살아있을 수 있었음. daemon=False 라 main thread 를 막을 위험 + 다른
    테스트의 `wait_for_pending` 결과 오염.

    각 테스트 시작 전후로 join + clear 로 격리. 테스트 자체가 fake_p4 + env
    분리를 안 하면 실 p4 호출이 일어날 수 있어 join 의 timeout 도 짧게.
    """
    # 시작 전 — 이전 테스트의 잔여 thread + dirty 플래그 정리.
    try:
        from whooing_tui import p4_sync, sync
        # 동기화 백엔드 명시 설정도 격리 — 기본 'none'(env-only 해석)로 복귀.
        # P4 동작을 검사하는 테스트는 자체적으로 backend='p4' 를 opt-in.
        sync.reset()
        # 빠르게 join (default 30s 는 너무 길어 명시 1s).
        p4_sync.wait_for_pending(timeout_per_thread=1.0)
        with p4_sync._PENDING_LOCK:
            p4_sync._PENDING.clear()
        # CL #52853+: 세션 dirty 플래그 — 이전 테스트가 set 한 채로 다음
        # 테스트의 `flush_on_exit skip` 검증을 오염시킬 수 있어 reset.
        p4_sync.reset_session_mutated()
        # CL #53093+: 세션 change journal 도 격리.
        p4_sync.clear_journal()
    except Exception:  # pragma: no cover
        pass
    yield
    # 종료 후 — 이 테스트가 spawn 한 thread 정리.
    try:
        from whooing_tui import p4_sync, sync
        p4_sync.wait_for_pending(timeout_per_thread=1.0)
        with p4_sync._PENDING_LOCK:
            p4_sync._PENDING.clear()
        p4_sync.reset_session_mutated()
        sync.reset()
    except Exception:  # pragma: no cover
        pass
