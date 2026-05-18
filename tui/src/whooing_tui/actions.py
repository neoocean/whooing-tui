"""Action helper 들 — 반복적 try/except → set_status 보일러플레이트 제거.

CL #52834+. 유지보수 감사 결과 (`docs/MAINTAINABILITY-REVIEW.md`) 의
1순위 후보. 종전엔 거의 모든 화면의 `action_*` / worker 가 같은 모양의
try/except 를 반복:

```python
@work(exclusive=True, group="...")
async def _submit_create(self, draft):
    try:
        await self._client.create_entry(...)
    except ToolError as e:
        self.set_status(f"... 실패 [{e.kind}] {e.message}", error=True)
        return
    except Exception as e:
        log.exception("...")
        self.set_status(f"... 실패 (INTERNAL): {e}", error=True)
        return
    self.set_status("성공.")
```

본 모듈의 `@safe_action(...)` 는 위 패턴을 데코레이터로:

```python
@safe_action("거래 생성")
async def _submit_create(self, draft):
    await self._client.create_entry(...)
    self.set_status("성공.")
```

설계 원칙:
- *옵트-인* — 기존 try/except 가 있는 함수는 *건드리지 않는다*. 새 코드 /
  명확히 단순화 가능한 곳에만 점진 적용. 일괄 적용은 위험 (각 함수의
  특수한 에러 분기를 무너뜨릴 수 있음).
- self.set_status(error=True) 컨벤션에 의존 — caller 가 화면 (Screen
  / ModalScreen) 이거나 그 메서드여야.
- 동기/비동기 모두 지원 — `inspect.iscoroutinefunction` 로 분기.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Awaitable, Callable

from whooing_tui.models import ToolError

log = logging.getLogger(__name__)


def safe_action(
    label: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """`action_*` / worker 의 try/except + set_status 보일러플레이트 제거.

    Args:
        label: 사용자에게 보일 작업 이름 (예: "거래 생성"). None 이면
               wrapped 함수의 `__name__` 사용.

    동작:
        - `ToolError`: `set_status("{label} 실패 [{kind}] {message}", error=True)`.
        - 그 외 `Exception`: `log.exception` + `set_status("{label} 실패
          (INTERNAL): {e}", error=True)`. 트레이스백을 사용자에게 노출하지
          않고 로그로만.
        - 정상 종료: 데코레이터는 아무 status 도 set 하지 않음 — 호출자가
          성공 시 메시지를 직접 set 한다 (실패만 기계적 처리).

    사용 예 (메서드 / async 메서드 모두):
        @safe_action("거래 삭제")
        async def _submit_delete(self, target):
            await self._client.delete_entry(...)
            self.set_status("삭제 완료.")
    """

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        op = label or fn.__name__

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def _async_wrap(self, *args, **kwargs):
                try:
                    return await fn(self, *args, **kwargs)
                except ToolError as e:
                    _safe_set_status(
                        self, f"{op} 실패 [{e.kind}] {e.message}",
                    )
                except Exception as e:
                    log.exception("%s failed", op)
                    _safe_set_status(
                        self, f"{op} 실패 (INTERNAL): {e}",
                    )
            return _async_wrap

        @functools.wraps(fn)
        def _sync_wrap(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except ToolError as e:
                _safe_set_status(
                    self, f"{op} 실패 [{e.kind}] {e.message}",
                )
            except Exception as e:
                log.exception("%s failed", op)
                _safe_set_status(
                    self, f"{op} 실패 (INTERNAL): {e}",
                )
        return _sync_wrap

    return _decorator


def _safe_set_status(target: Any, msg: str) -> None:
    """target.set_status(msg, error=True) 호출. set_status 가 없으면 silent.

    Screen / Modal 외 (예: 테스트의 stub) 에서도 안전하도록 hasattr 검사.
    """
    set_status = getattr(target, "set_status", None)
    if callable(set_status):
        try:
            set_status(msg, error=True)
        except Exception:  # pragma: no cover — set_status 자체가 raise 면 silent.
            log.debug("safe_action: set_status failed", exc_info=True)
    else:
        log.warning("safe_action: target lacks set_status (msg=%r)", msg)


__all__ = ["safe_action"]
