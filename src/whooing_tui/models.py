"""Pydantic models — TUI 가 다루는 후잉 도메인의 최소 집합.

응답 스키마는 엔드포인트별로 약간씩 다르고 일부 필드는 sparse 하므로 모든
모델에 `extra = "allow"` 를 둔다 (실 응답 누락 필드를 무시하지 않고 보존).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Section(BaseModel):
    """후잉 섹션 (가계부) — 최소 필드만."""

    model_config = ConfigDict(extra="allow")

    section_id: str
    title: str | None = None


class Account(BaseModel):
    """후잉 계정과목 (account_id) — `x` 접두사 ID + 표시명 + 분류.

    type 은 후잉 응답의 키 (assets / liabilities / capital / income /
    expenses / group) 를 그대로 반영한다.
    """

    model_config = ConfigDict(extra="allow")

    account_id: str
    title: str = ""
    type: str = ""


class Entry(BaseModel):
    """후잉 거래 항목 — 핵심 필드만."""

    model_config = ConfigDict(extra="allow")

    entry_id: str | None = None
    section_id: str | None = None
    entry_date: str | None = None  # YYYYMMDD
    money: int | None = None
    item: str = ""
    memo: str = ""
    l_account_id: str | None = None
    r_account_id: str | None = None


class ToolError(Exception):
    """TUI / 헤드리스 CLI 가 사용자에게 노출하는 에러.

    `kind` 는 안정된 enum 문자열 (분기 가능):
      - USER_INPUT  : 입력 파라미터 오류
      - AUTH        : 자격증명 만료/거부
      - RATE_LIMIT  : 분당/일일 한도
      - UPSTREAM    : 후잉 서버 오류 / 비예상 응답
      - INTERNAL    : 본 도구의 버그
    """

    def __init__(self, kind: str, message: str, **details: Any) -> None:
        self.kind = kind
        self.message = message
        self.details = details
        super().__init__(f"[{kind}] {message}")
