"""한글 IME (두벌식) ↔ 영문 키 매핑 + Binding helper.

macOS / Linux 환경에서 사용자가 한글 IME 를 켜둔 채 같은 물리 키를
누르면 textual 의 key event 의 character 가 한글 자모로 들어온다 — 영문
binding ("q", "s" 등) 이 매칭 안 됨.

본 모듈은:
  - `KOREAN_OF`: 영문 → 한글 자모 매핑 (두벌식 표준).
  - `KOREAN_TO_EN`: 역방향 (한글 자모 → 영문) — `on_key` 인터셉트 시 사용.
  - `bind_ko(en_key, action, description, **kwargs)`: 영문 binding 1개 +
    한글 자모 binding 1개 (show=False, **priority=True**) 의 list 반환.

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


# Hangul Syllables block (U+AC00 ~ U+D7A3) — 11,172 음절.
_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3

# 초성 (Choseong) 19 자 — Unicode 분해 알고리즘에서 syllable_index // 588.
_CHOSEONG: tuple[str, ...] = (
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
)


def choseong_of(ch: str) -> str:
    """단일 글자의 초성 — 한글 음절이면 자모 1개, 그 외는 그대로.

    예: '스' → 'ㅅ', '벅' → 'ㅂ', 'A' → 'A', '!' → '!'.
    """
    if not ch:
        return ""
    c = ord(ch[0])
    if _HANGUL_BASE <= c <= _HANGUL_LAST:
        i = c - _HANGUL_BASE
        return _CHOSEONG[i // 588]
    return ch[0]


def to_choseong_string(s: str) -> str:
    """문자열 전체의 초성 만 — 한글은 자모, 영문/숫자/punct 는 그대로.

    예: '스타벅스' → 'ㅅㅌㅂㅅ', '카페' → 'ㅋㅍ',
        '한국T맵' → 'ㅎㄱTㅁ', 'ABC' → 'ABC'.
    """
    return "".join(choseong_of(c) for c in (s or ""))


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

# 역방향 lookup — `on_key` 인터셉트 시 한글 → 영문 원키 회수용.
KOREAN_TO_EN: dict[str, str] = {ko: en for en, ko in KOREAN_OF.items()}


def bind_ko(
    en_key: str,
    action: str,
    description: str = "",
    **kwargs,
) -> list[Binding]:
    """영문 + 한글 자모 binding 둘 다 만들어 list 반환.

    한글 binding 은 Footer 에 노출하지 않는다 (`show=False`) — 영문 키만
    화면에 보이고, 한글 IME 일 때도 같은 키 입력으로 같은 액션이 동작한다.

    **CL #51115+: 한글 binding 은 항상 `priority=True`** — focused widget
    (Input / DataTable type-to-search 등) 이 한글 자모를 텍스트로 흡수해
    화면에 잠깐 표시되는 시각 지연 을 막는다. 영문 쪽의 `priority` 는 호출
    측이 결정한 그대로 유지 (영문 키는 보통 텍스트 입력란에서도 의미가
    있어 priority 까지는 강제 안 함).

    `priority` 외의 kwargs (예: `show`) 는 양쪽 binding 에 그대로 전달.
    `en_key` 가 매핑에 없으면 영문 binding 만 1개.
    """
    out = [Binding(en_key, action, description, **kwargs)]
    ko = KOREAN_OF.get(en_key)
    if ko:
        ko_kwargs = dict(kwargs)
        ko_kwargs["show"] = False  # 영문만 Footer 에 노출
        ko_kwargs["priority"] = True  # focused widget 보다 우선 — 시각 지연 방지
        out.append(Binding(ko, action, description, **ko_kwargs))
    return out
