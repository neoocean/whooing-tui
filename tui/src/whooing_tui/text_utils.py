"""텍스트 / 한글 / 약어 helper 들 (CL #52834+).

CL #51158 / CL #51125 의 pure helper 들이 `screens/entries_compact.py`
에 살고 있어 호출처 (EntriesScreen / DupeEvalScreen 등) 가 screen 모듈을
import 하는 어색한 결합이 있었다. 본 모듈로 *re-export* 해 import 경로를
정규화한다 — entries_compact 의 정의 자체는 그대로 두어 (CL 분리 + 회귀
방어). 호출처는 새 import 경로를 사용한다.

```
from whooing_tui.text_utils import is_hangul, abbreviate_account_name
```

본 모듈은 *순수* (Textual / sqlite / 후잉 의존 없음). 단위 테스트가 빠르고
다른 모듈에서 자유롭게 import 가능.
"""

from __future__ import annotations

# screens/entries_compact 의 pure helpers 재노출 — 본 모듈이 정식 import
# 경로. entries_compact 는 layout 계산 / threshold table 도 함께 가지므로
# 그대로 유지 (분리 효과 X — re-export 만 정상화).
from whooing_tui.screens.entries_compact import (
    abbreviate_account_name,
    column_is_visible,
    compute_compact_level,
    hidden_columns_for_level,
    is_hangul,
)

from typing import Any


def fmt_money(v: Any) -> str:
    """후잉 money(정수 KRW) → 천단위 콤마 평문 (감사 2026-06 §1-B 단일화).

    `None`/빈문자 → `""`, 비숫자 → `str(v)`. Rich markup 이 필요한 보고서
    뷰는 `reports._fmt_money`(음수 빨강/0 dim) 를 별도로 쓴다.
    """
    if v is None or v == "":
        return ""
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


__all__ = [
    "abbreviate_account_name",
    "column_is_visible",
    "compute_compact_level",
    "fmt_money",
    "hidden_columns_for_level",
    "is_hangul",
]
