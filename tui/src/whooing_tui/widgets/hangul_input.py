"""한글 자모 조합 처리하는 Input mixin/subclass.

CL #52781+. 사용자 보고:
> 아이폰 Blink 앱에서 텍스트박스에서 한글이 조합되지 않고 모두 풀려서
> 입력됩니다.

배경: Blink Shell 같은 일부 iOS terminal 가 한국어 IME keystroke 를
완성 음절이 아니라 자모 (Hangul Compat Jamo) 로 보낸다. textual 의
Input 은 받은 그대로 표시 — 사용자에겐 풀어진 자모. `core.hangul.
compose_hangul()` 이 자모 sequence 를 음절로 합성하는 pure 함수,
본 모듈이 그것을 textual Input 의 value reactive 에 통합한다.

사용:

    from whooing_tui.widgets.hangul_input import enable_hangul_composing

    # App.on_mount 또는 process 시작 시 한 번 호출 — 전역 적용.
    enable_hangul_composing()

이후 모든 `textual.widgets.Input` 인스턴스의 `value` 가 자모 → 음절 변환.
"""

from __future__ import annotations

import logging

from textual.widgets import Input

from whooing_core.hangul import compose_hangul

log = logging.getLogger(__name__)


_ORIG_WATCH_VALUE = None
_ENABLED = False


def enable_hangul_composing() -> None:
    """Input.watch_value 를 wrap — `value` 가 변경될 때마다 자모 조합 적용.

    Idempotent — 여러 번 호출해도 한 번만 wrap.

    동작:
      1. user 가 'ㅎ' 입력 → `Input.value = "ㅎ"` (textual 내부)
      2. textual 의 watch_value 가 호출됨 — 본 wrapper 가 가로채서
         `compose_hangul("ㅎ") == "ㅎ"` (단독 자모 — 그대로) 반환,
         그것이 동일하면 noop.
      3. user 가 'ㅏ' 추가 → `value = "ㅎㅏ"` → `compose_hangul` → `"하"`.
      4. wrapper 가 `Input._value` 에 합성된 값을 직접 set (recursive
         avoid using `with self.prevent(Input.Changed)`).

    구현 주의:
      - reactive descriptor 라 attribute 직접 set 이 watch 재호출.
        `with self.prevent(...)` 로 차단.
      - 자모-only 단독 입력 (`ㅎ` 한 글자) 은 그대로 — 사용자가 다음 자모
        를 칠 동안 표시. 완성 음절도 그대로 통과.
    """
    global _ORIG_WATCH_VALUE, _ENABLED
    if _ENABLED:
        return

    _ORIG_WATCH_VALUE = getattr(Input, "watch_value", None)

    def _patched_watch_value(self: Input, value: str) -> None:
        # 원본 watch 호출 — selection / cursor 등 textual 의 내부 처리 보존.
        if _ORIG_WATCH_VALUE is not None:
            try:
                _ORIG_WATCH_VALUE(self, value)
            except Exception:  # pragma: no cover — textual 내부 예외
                log.debug("orig watch_value failed", exc_info=True)
        # 자모 조합 시도.
        try:
            composed = compose_hangul(value)
        except Exception:  # pragma: no cover — pure 함수라 거의 발생 X
            log.debug("compose_hangul failed", exc_info=True)
            return
        if composed == value:
            return
        # value 가 바뀌어야 — 재귀 watch 차단 후 직접 set.
        # textual 의 reactive 는 `self._reactives["value"] = composed` 같은
        # 직접 패치보다 `self.value = ...` 가 안전하지만 watch 가 다시 발화.
        # `with self.prevent(Input.Changed)` 로 차단 + 이벤트 발사 안 함.
        try:
            with self.prevent(Input.Changed):
                self.value = composed
                # cursor 를 end 로 — 사용자가 다음 글자 칠 자리 자연.
                try:
                    self.cursor_position = len(composed)
                except Exception:  # pragma: no cover
                    pass
        except Exception:  # pragma: no cover
            log.debug("prevent+set failed", exc_info=True)

    Input.watch_value = _patched_watch_value  # type: ignore[assignment]
    _ENABLED = True


def disable_hangul_composing() -> None:
    """원복 — 테스트용. production 에서는 호출 안 함."""
    global _ORIG_WATCH_VALUE, _ENABLED
    if not _ENABLED:
        return
    if _ORIG_WATCH_VALUE is not None:
        Input.watch_value = _ORIG_WATCH_VALUE  # type: ignore[assignment]
    _ENABLED = False
