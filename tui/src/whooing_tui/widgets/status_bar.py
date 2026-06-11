"""StatusBarMixin — 화면 하단 status Static 갱신 공통화 (감사 2026-06 §1-A).

각 ModalScreen 이 동일한 `_set_status(text, error/warn)` 보일러플레이트를
복제하던 것을 한 곳으로. 서브클래스는 `STATUS_ID` (status `Static` 의 `#id`)
만 지정한다. CSS 에 `<#id>.error` / `<#id>.warn` 색을 정의해 두면 자동 반영.

```python
class MyScreen(StatusBarMixin, ModalScreen[None]):
    STATUS_ID = "#my_status"
    ...
    self._set_status("완료")            # 평상
    self._set_status("실패", error=True)  # 빨강
    self._set_status("주의", warn=True)   # 노랑
```

`last_status` (평문) 도 함께 보관해 테스트가 markup 없이 검증한다.
"""

from __future__ import annotations

from textual.widgets import Static


class StatusBarMixin:
    """`STATUS_ID` 가 가리키는 Static 을 error/warn 색과 함께 갱신."""

    STATUS_ID: str = "#status"
    last_status: str = ""

    def _set_status(
        self, text: str, *, error: bool = False, warn: bool = False,
    ) -> None:
        self.last_status = text
        bar = self.query_one(self.STATUS_ID, Static)  # type: ignore[attr-defined]
        bar.update(text)
        bar.remove_class("error")
        bar.remove_class("warn")
        if error:
            bar.add_class("error")
        elif warn:
            bar.add_class("warn")
