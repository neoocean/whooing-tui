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
    yield
