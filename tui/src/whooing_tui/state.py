"""런타임 세션 상태.

TUI 와 헤드리스 CLI 모두 동일한 SessionState 를 공유한다. 핵심 책무:
  - 현재 활성 섹션 (section_id)
  - 계정과목 캐시 (account_id ↔ title 양방향) — accounts-list 1회 호출 결과
  - 마지막 fetch 시각 (선택) — 전 세션 cache invalidation 정책 미정

스레드 안전성은 보장하지 않는다. Textual 의 단일 이벤트 루프 / CLI 의 단일
asyncio task 안에서만 사용.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
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
    """`.env` 의 WHOOING_SECTION_ID. 없으면 None (첫 섹션 자동 선택)."""
    val = (os.getenv("WHOOING_SECTION_ID") or "").strip()
    return val or None
