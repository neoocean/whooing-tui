"""후잉 REST 응답 / 거래 도메인 dict 의 TypedDict 정의 (CL #52834+).

종전엔 `client.py` 의 모든 메서드가 `dict[str, Any]` / `list[dict[str, Any]]`
를 반환 — LLM / IDE / mypy 가 필드 이름을 알 수 없었다. 본 모듈은 가장
많이 쓰이는 shape 만 TypedDict 로 명시 — 점진적 narrowing 의 첫 단계.

설계:
- *기존 코드를 깨지 않는다* — TypedDict 는 런타임 dict 와 호환이라 모든
  기존 호출자가 그대로 동작.
- *선택 적용* — 새 코드 / refactor 하는 함수만 인자/반환 타입을 본
  TypedDict 로 좁힌다. 일괄 적용 X.
- *Optional 은 `NotRequired`* — 후잉 응답이 sparse 한 필드는 누락 가능.

타입 narrowing 예:
```python
from whooing_tui.responses import EntryDict

async def update_entry(...) -> EntryDict:
    raw = await self._client.update_entry(...)
    return raw  # type: ignore[return-value]
```
"""

from __future__ import annotations

from typing import TypedDict

try:
    from typing import NotRequired
except ImportError:  # pragma: no cover — Python 3.10 fallback
    from typing_extensions import NotRequired  # type: ignore


class EntryDict(TypedDict):
    """후잉 단일 거래 — `entries-list` / `entries-create` 응답에서.

    money 는 int (KRW), 음수 가능 (환불). entry_date 는 "YYYYMMDD" 8자.
    item / memo 는 자유 입력. l_account / r_account 는 type 키
    ("assets" / "liabilities" / "expenses" / "income" / "capital").
    """

    entry_id: str
    entry_date: str
    money: int
    l_account: str
    l_account_id: str
    r_account: str
    r_account_id: str
    item: NotRequired[str]
    memo: NotRequired[str]
    # 일부 응답에서만 등장 (DB-internal):
    section_id: NotRequired[str]
    create_date: NotRequired[str]
    modify_date: NotRequired[str]


class SectionDict(TypedDict):
    """후잉 섹션 — `sections-list` 응답의 각 원소."""

    section_id: str
    title: str
    # 정렬 / 메타:
    sort: NotRequired[int]
    use_yn: NotRequired[str]


class AccountDict(TypedDict):
    """후잉 계정과목 — `accounts-list` 응답의 각 type bucket 안 원소.

    `type` 키는 본 dict 에서는 보존돼 있지 않을 수 있음 — caller 가
    bucket key 로부터 외부에서 보강 (예: `flatten_accounts` 가 `type` 컬럼
    주입).
    """

    account_id: str
    title: str
    # 표시 / 분류:
    type: NotRequired[str]
    type_key: NotRequired[str]
    sort: NotRequired[int]
    use_yn: NotRequired[str]
    # 잔액 (계정 위계 / 잔액 컬럼이 응답에 들어올 때):
    balance: NotRequired[int]


class AccountsByType(TypedDict, total=False):
    """`accounts-list` 의 결과 — type 별 list 사전.

    not-total — 모든 type 이 항상 존재하지는 않음 (사용자가 안 만든 type).
    """

    assets: list[AccountDict]
    liabilities: list[AccountDict]
    expenses: list[AccountDict]
    income: list[AccountDict]
    capital: list[AccountDict]


class CreateEntryResponse(TypedDict, total=False):
    """`entries-create` 응답 — shape 이 다양해 모든 필드는 NotRequired.

    `_extract_entry_id` 가 다음 우선순위로 entry_id 검색:
    `entry_id` → `entries[0].entry_id` → `results[0].entry_id` →
    `data[0].entry_id`.
    """

    entry_id: str
    entries: list[EntryDict]
    results: list[EntryDict]
    data: list[EntryDict]


__all__ = [
    "AccountDict",
    "AccountsByType",
    "CreateEntryResponse",
    "EntryDict",
    "SectionDict",
]
