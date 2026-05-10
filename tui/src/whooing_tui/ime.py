"""한글 IME (두벌식) ↔ 영문 키 매핑 + Binding helper.

macOS / Linux 환경에서 사용자가 한글 IME 를 켜둔 채 같은 물리 키를
누르면 textual 의 key event 의 character 가 한글 자모로 들어온다 — 영문
binding ("q", "s" 등) 이 매칭 안 됨.

본 모듈은:
  - `KOREAN_OF`: 영문 → 한글 자모 매핑 (두벌식 표준).
  - `bind_ko(en_key, action, description, **kwargs)`: 영문 binding 1개 +
    한글 자모 binding 1개 (show=False 로 Footer 안 노출) 의 list 를 반환.

사용:

    BINDINGS = [
        *bind_ko("q", "back", "Quit", show=True),
        *bind_ko("s", "open_sections", "Sections", show=True, priority=True),
        Binding("escape", "back", show=False),  # IME 영향 없는 키는 그대로
    ]

영문에 매핑되지 않는 키 (`escape`, `enter`, `ctrl+s`, `+`, `-`,
`question_mark` 등) 는 IME 영향이 없으므로 매핑하지 않는다 — 일반
`Binding(...)` 그대로.
"""

from __future__ import annotations

from textual.binding import Binding


# 두벌식 표준: 영문 (소문자) → 한글 자모.
# Shift 조합 (대문자 → 쌍자음 / 합쳐진 모음) 은 우리 단축키에 쓰이지
# 않으므로 생략. 합쳐진 모음 (예: ㅢ = ㅡ + ㅣ) 도 단일 자모 매핑이
# 아니라 input method 의 조합 결과라 여기서는 다루지 않는다.
KOREAN_OF: dict[str, str] = {
    "q": "ㅂ", "w": "ㅈ", "e": "ㄷ", "r": "ㄱ", "t": "ㅅ",
    "y": "ㅛ", "u": "ㅕ", "i": "ㅑ", "o": "ㅐ", "p": "ㅔ",
    "a": "ㅁ", "s": "ㄴ", "d": "ㅇ", "f": "ㄹ", "g": "ㅎ",
    "h": "ㅗ", "j": "ㅓ", "k": "ㅏ", "l": "ㅣ",
    "z": "ㅋ", "x": "ㅌ", "c": "ㅊ", "v": "ㅍ", "b": "ㅠ",
    "n": "ㅜ", "m": "ㅡ",
}


def bind_ko(
    en_key: str,
    action: str,
    description: str = "",
    **kwargs,
) -> list[Binding]:
    """영문 + 한글 자모 binding 둘 다 만들어 list 반환.

    한글 binding 은 Footer 에 노출하지 않는다 (`show=False`) — 영문 키만
    화면에 보이고, 한글 IME 일 때도 같은 키 입력으로 같은 액션이 동작한다.
    `priority` 등 다른 kwargs 는 양쪽 binding 에 그대로 전달.

    `en_key` 가 매핑에 없으면 영문 binding 만 1개 (single-element list).
    이 경우는 `Binding(...)` 직접 사용하는 것과 동등하나, `bind_ko` 의
    list-반환 일관성을 위해 그대로 둔다.
    """
    out = [Binding(en_key, action, description, **kwargs)]
    ko = KOREAN_OF.get(en_key)
    if ko:
        ko_kwargs = dict(kwargs)
        ko_kwargs["show"] = False  # 영문만 Footer 에 노출
        out.append(Binding(ko, action, description, **ko_kwargs))
    return out
