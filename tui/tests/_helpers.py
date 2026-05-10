"""Shared test helpers — CL #51157+ (review C7+C8+C9).

종전엔 `_FakeClient`, `_wait_for`, fake_p4 shell script setup 이 10+ 파일에서
중복. 본 모듈이 단일 출처 — 각 테스트는 `from tests._helpers import (...)`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


# ---- async polling helper ------------------------------------------------


async def wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    """조건이 True 가 될 때까지 polling. timeout 초과 시 False.

    종전 10+ 테스트 파일이 자체 `_wait_for` 정의 — 본 helper 가 단일 출처.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


# ---- FakeWhooingClient ---------------------------------------------------


class FakeWhooingClient:
    """후잉 client mock — 테스트에서 실 후잉 호출 회피.

    종전엔 각 테스트 파일마다 자체 `_FakeClient` (`test_account_picker`,
    `test_attachment_browser`, `test_accounts_screen`, `test_cli`,
    `test_budget_edit`, `test_entries_tag_inline`, `test_entries_mutate`,
    `test_goal_edit`, `test_help_modal`, `test_monthly_entries` 등 10+).
    본 클래스가 base — 추가 override 가 필요한 테스트는 subclass.

    제공 endpoints (모두 `async`):
      - list_sections / list_accounts / list_entries
      - create_entry / update_entry / delete_entry
      - list_monthly / create_monthly / delete_monthly
      - get_budget / set_budget / delete_budget
      - get_budget_goal / set_budget_goal / get_goal / set_goal

    호출 추적: 각 mutating endpoint 가 `*_calls` list 에 인자 dict 보관.
    """

    def __init__(
        self,
        sections: list[dict[str, Any]] | None = None,
        accounts_by_section: dict[str, dict[str, Any]] | None = None,
        entries_by_section: dict[str, list[dict[str, Any]]] | None = None,
        entries_error: Exception | None = None,
        monthly_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        # 기본값.
        self.sections = (
            sections if sections is not None
            else [{"section_id": "s1", "title": "main"}]
        )
        self.accounts_by_section = accounts_by_section or {
            "s1": {
                "assets": [{"account_id": "x11", "title": "현금"}],
                "expenses": [
                    {"account_id": "x20", "title": "식비"},
                    {"account_id": "x21", "title": "교통비"},
                ],
            },
        }
        self.entries_by_section = entries_by_section or {}
        self.entries_error = entries_error
        self._monthly = monthly_rows or []
        # 호출 추적.
        self.list_sections_calls: list[None] = []
        self.list_accounts_calls: list[str] = []
        self.list_entries_calls: list[tuple[str, str, str]] = []
        self.create_entry_calls: list[dict[str, Any]] = []
        self.update_entry_calls: list[dict[str, Any]] = []
        self.delete_entry_calls: list[dict[str, Any]] = []
        self.list_monthly_calls: list[str] = []
        self.create_monthly_calls: list[dict[str, Any]] = []
        self.delete_monthly_calls: list[tuple[str, str]] = []
        self.set_budget_calls: list[dict[str, Any]] = []
        self.delete_budget_calls: list[dict[str, Any]] = []
        self.set_budget_goal_calls: list[dict[str, Any]] = []
        self.set_goal_calls: list[dict[str, Any]] = []
        # alias (legacy 테스트 호환).
        self.delete_calls = self.delete_entry_calls

    # --- read endpoints ---

    async def list_sections(self) -> list[dict[str, Any]]:
        self.list_sections_calls.append(None)
        return list(self.sections)

    async def list_accounts(self, section_id: str) -> dict[str, Any]:
        self.list_accounts_calls.append(section_id)
        return self.accounts_by_section.get(section_id, {})

    async def list_entries(
        self, section_id: str, start_date: str, end_date: str,
    ) -> list[dict[str, Any]]:
        self.list_entries_calls.append((section_id, start_date, end_date))
        if self.entries_error:
            raise self.entries_error
        return list(self.entries_by_section.get(section_id, []))

    # --- mutating entry endpoints ---

    async def create_entry(self, **kwargs):
        self.create_entry_calls.append(kwargs)
        return {"entry_id": f"e_new_{len(self.create_entry_calls)}", **kwargs}

    async def update_entry(self, **kwargs):
        self.update_entry_calls.append(kwargs)
        return {**kwargs}

    async def delete_entry(self, **kwargs):
        self.delete_entry_calls.append(kwargs)
        return {"status": "ok"}

    # --- monthly endpoints ---

    async def list_monthly(self, *, section_id: str) -> list[dict[str, Any]]:
        self.list_monthly_calls.append(section_id)
        return list(self._monthly)

    async def create_monthly(self, **kwargs):
        self.create_monthly_calls.append(kwargs)
        # 새 id 는 기존 monthly + create 호출 모두 합쳐 unique.
        new_id = f"m{len(self._monthly) + len(self.create_monthly_calls)}"
        new = {"id": new_id, **kwargs}
        self._monthly.append(new)
        return new

    async def delete_monthly(self, *, section_id: str, monthly_id: str):
        self.delete_monthly_calls.append((section_id, monthly_id))
        self._monthly = [
            r for r in self._monthly
            if str(r.get("id") or r.get("monthly_id")) != monthly_id
        ]
        return {"status": "ok"}

    # --- budget / goal endpoints ---

    async def get_budget(
        self, *, section_id: str, account: str,
        start_date: str | None = None, end_date: str | None = None,
    ) -> dict[str, Any]:
        return {"rows": []}

    async def set_budget(self, **kwargs):
        self.set_budget_calls.append(kwargs)
        return {"status": "ok"}

    async def delete_budget(self, **kwargs):
        self.delete_budget_calls.append(kwargs)
        return {"status": "ok"}

    async def get_budget_goal(self, *, section_id: str) -> dict[str, Any]:
        return {}

    async def set_budget_goal(self, **kwargs):
        self.set_budget_goal_calls.append(kwargs)
        return {"status": "ok"}

    async def get_goal(
        self, *, section_id: str,
        start_date: str | None = None, end_date: str | None = None,
    ) -> Any:
        return {"rows": []}

    async def set_goal(self, **kwargs):
        self.set_goal_calls.append(kwargs)
        return {"status": "ok"}


# ---- fake_p4 shell script factory ---------------------------------------


def make_fake_p4(
    tmp_path: Path,
    *,
    log_file: Path | None = None,
    where_returns: int = 0,
    other_returns: int = 0,
) -> Path:
    """tmp_path 안에 가짜 `p4` shell script 생성.

    CL #51157+ (review C9). `test_p4_sync.py` 의 15회 반복 setup 통합.
    `WHOOING_P4_BIN=<returned_path>` 로 set 하면 모든 p4 호출이 본 script 로.

    Args:
      tmp_path: pytest tmp_path fixture.
      log_file: 호출 args 를 append 할 파일 (None = 로깅 없음).
      where_returns: `p4 where` 의 exit code (0 = 매핑됨, 1 = 매핑 외).
      other_returns: 다른 명령들의 exit code.

    Returns:
      shell script 의 Path. 호출자가 `monkeypatch.setenv("WHOOING_P4_BIN", ...)`.
    """
    fake = tmp_path / "p4"
    log_redirect = f"echo \"$@\" >> {log_file}\n" if log_file else ""
    if where_returns == other_returns:
        # 단순 케이스 — 모든 명령 같은 exit.
        body = f"#!/bin/sh\n{log_redirect}exit {where_returns}\n"
    else:
        # where 분기.
        body = (
            f"#!/bin/sh\n"
            f"{log_redirect}"
            f"if [ \"$1\" = \"where\" ]; then exit {where_returns}; fi\n"
            f"exit {other_returns}\n"
        )
    fake.write_text(body)
    fake.chmod(0o755)
    return fake
