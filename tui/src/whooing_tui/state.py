"""런타임 세션 상태 + 영구 사용자 상태 (state.json).

TUI 와 헤드리스 CLI 모두 동일한 SessionState 를 공유한다. 핵심 책무:
  - 현재 활성 섹션 (section_id)
  - 계정과목 캐시 (account_id ↔ title 양방향) — accounts-list 1회 호출 결과
  - 마지막 fetch 시각 (선택) — 전 세션 cache invalidation 정책 미정

부가로 영구 사용자 상태 (last_section_id 등) 를 `~/.config/whooing-tui/
state.json` 에 보관 — `load_last_section_id()` / `save_last_section_id()`.
사용자가 `s` 키로 명시 선택한 섹션이 다음 부팅에 자동 복원되도록.

스레드 안전성은 보장하지 않는다. Textual 의 단일 이벤트 루프 / CLI 의 단일
asyncio task 안에서만 사용.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class SessionState:
    """세션 동안 유지되는 후잉 컨텍스트."""

    # 현재 활성 섹션. None 이면 아직 sections-list 가 끝나지 않았음을 의미.
    section_id: str | None = None
    section_title: str | None = None

    # 활성 섹션의 계정과목. accounts-list 응답을 그대로 캐시.
    accounts_raw: dict[str, Any] = field(default_factory=dict)
    accounts_flat: list[dict[str, str]] = field(default_factory=list)

    # account_id → title, title → account_id (대소문자 무시) 빠른 조회.
    _id_to_title: dict[str, str] = field(default_factory=dict, repr=False)
    _title_to_id: dict[str, str] = field(default_factory=dict, repr=False)

    def set_section(self, section_id: str, title: str | None = None) -> None:
        """섹션 전환. 계정과목 캐시는 자동으로 무효화된다."""
        if section_id != self.section_id:
            log.info("section 전환: %s → %s (%s)",
                     self.section_id, section_id, title)
            self.accounts_raw = {}
            self.accounts_flat = []
            self._id_to_title.clear()
            self._title_to_id.clear()
        self.section_id = section_id
        self.section_title = title

    def set_accounts(
        self,
        accounts_raw: dict[str, Any],
        accounts_flat: list[dict[str, str]],
    ) -> None:
        """accounts-list 결과를 세션에 저장하고 양방향 인덱스 빌드."""
        self.accounts_raw = accounts_raw
        self.accounts_flat = accounts_flat
        self._id_to_title = {a["account_id"]: a["title"] for a in accounts_flat}
        self._title_to_id = {a["title"].lower(): a["account_id"] for a in accounts_flat}

    def title_of(self, account_id: str) -> str:
        """account_id 의 표시명. 없으면 account_id 자체를 반환 (디버깅 친화)."""
        return self._id_to_title.get(account_id, account_id)

    def id_of(self, title: str) -> str | None:
        """title (대소문자 무시) → account_id. 없으면 None."""
        return self._title_to_id.get(title.lower())


def default_section_id_from_env() -> str | None:
    """`.env` 의 WHOOING_SECTION_ID. 없으면 None.

    EntriesScreen 의 자동 활성화에서는 (CL #51031+) saved last_section /
    "Default" 명 매칭 / 응답의 is_default 플래그를 우선 시도하고, 본 환경
    변수는 그것들이 모두 매칭 안 됐을 때의 마지막 fallback (첫 섹션 직전).
    """
    val = (os.getenv("WHOOING_SECTION_ID") or "").strip()
    return val or None


# ---- 영구 사용자 상태 (state.json) -------------------------------------


def _state_path() -> Path:
    """영구 상태 파일 경로 — `$XDG_CONFIG_HOME/whooing-tui/state.json`.

    XDG_CONFIG_HOME 미설정 시 `~/.config/whooing-tui/state.json`. cache 와
    분리된 글로벌 위치 — `make clean` 이나 cache 디렉토리 삭제로 영향받지
    않는다.
    """
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "whooing-tui" / "state.json"


def load_state() -> dict[str, Any]:
    """state.json 을 dict 로 로드. 없거나 깨졌으면 빈 dict."""
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            log.warning("state.json 이 dict 가 아님 — 무시 (%s)", p)
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.warning("state.json 로드 실패 — 무시: %s", e)
        return {}


def save_state(state: dict[str, Any]) -> None:
    """state dict 를 state.json 에 atomic write (실패 시 경고만)."""
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # tmp + rename 으로 partial write 방지
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError as e:
        log.warning("state.json 저장 실패: %s", e)


def load_last_section_id() -> str | None:
    """마지막에 활성화됐던 section_id. 한 번도 저장 안 됐으면 None."""
    val = (load_state().get("last_section_id") or "").strip()
    return val or None


def save_last_section_id(section_id: str) -> None:
    """section_id 를 state.json 에 영구 저장. 다음 부팅 시 복원."""
    if not section_id:
        return
    state = load_state()
    if state.get("last_section_id") == section_id:
        return  # 이미 같은 값 — write skip (잦은 set_section 호출 시 io 절약)
    state["last_section_id"] = section_id
    state.setdefault("version", 1)
    save_state(state)
