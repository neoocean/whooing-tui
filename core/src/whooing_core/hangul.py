"""한글 자모 조합 — iOS Blink 같이 자모가 분리되어 들어오는 환경 fix.

CL #52781+. 사용자 보고:
> 아이폰 Blink 앱에서 실행할 때 앱 내의 텍스트박스에서 한글이 조합되지
> 않고 모두 풀려서 입력됩니다.

원인: Blink Shell 같은 일부 iOS terminal 가 한국어 IME 의 keystroke 를
완성된 음절 (예: `한`) 이 아니라 **호환 자모** (`ㅎㅏㄴ`, Hangul
Compatibility Jamo U+3130~U+318F) 로 보냄. textual 의 Input 위젯은
받은 그대로 표시 — 사용자에겐 모든 키가 풀어져 보임.

해결: 자모 sequence 를 음절로 합성. Compat Jamo (U+3130~) 를 Hangul
Jamo (U+1100~) 로 매핑한 뒤 state machine 으로 (초성, 중성, 종성) 결합.

호출 측 (`whooing_tui.widgets.hangul_input.HangulComposingInput`) 이 매
keystroke 후 `value` 에 본 함수를 적용. 사용자가 `ㅎ`/`ㅏ`/`ㄴ` 3 글자
입력 → 본 함수가 `한` 1글자로 변환 → Input.value = "한".

본 모듈은 pure — sqlite / httpx / textual 의존 없음. 단위 테스트 가능.
"""

from __future__ import annotations

from typing import Final

# Hangul Jamo blocks.
_CHO_BASE: Final = 0x1100
_JUNG_BASE: Final = 0x1161
# 종성 idx: 0 = 종성 없음, 1 = ㄱ (U+11A8), ..., 27 = ㅎ (U+11C2).
# 따라서 jong_codepoint == 0 이면 종성 없음, 그 외엔 idx = cp - 0x11A8 + 1.
_JONG_FIRST: Final = 0x11A8
_JONG_LAST: Final = 0x11C2
_SYLL_BASE: Final = 0xAC00
_SYLL_COUNT: Final = 11172   # 19 × 21 × 28

# 초성 19개 (U+1100 ~ U+1112).
_CHO_LAST: Final = 0x1112
# 중성 21개 (U+1161 ~ U+1175).
_JUNG_LAST: Final = 0x1175

# Compat Jamo → Hangul Jamo 초성 (Choseong).
_COMPAT_TO_CHO: Final[dict[int, int]] = {
    0x3131: 0x1100,  # ㄱ
    0x3132: 0x1101,  # ㄲ
    0x3134: 0x1102,  # ㄴ
    0x3137: 0x1103,  # ㄷ
    0x3138: 0x1104,  # ㄸ
    0x3139: 0x1105,  # ㄹ
    0x3141: 0x1106,  # ㅁ
    0x3142: 0x1107,  # ㅂ
    0x3143: 0x1108,  # ㅃ
    0x3145: 0x1109,  # ㅅ
    0x3146: 0x110A,  # ㅆ
    0x3147: 0x110B,  # ㅇ
    0x3148: 0x110C,  # ㅈ
    0x3149: 0x110D,  # ㅉ
    0x314A: 0x110E,  # ㅊ
    0x314B: 0x110F,  # ㅋ
    0x314C: 0x1110,  # ㅌ
    0x314D: 0x1111,  # ㅍ
    0x314E: 0x1112,  # ㅎ
}

# Compat Jamo → Hangul Jamo 중성 (Jungseong).
_COMPAT_TO_JUNG: Final[dict[int, int]] = {
    0x314F: 0x1161, 0x3150: 0x1162, 0x3151: 0x1163, 0x3152: 0x1164,
    0x3153: 0x1165, 0x3154: 0x1166, 0x3155: 0x1167, 0x3156: 0x1168,
    0x3157: 0x1169, 0x3158: 0x116A, 0x3159: 0x116B, 0x315A: 0x116C,
    0x315B: 0x116D, 0x315C: 0x116E, 0x315D: 0x116F, 0x315E: 0x1170,
    0x315F: 0x1171, 0x3160: 0x1172, 0x3161: 0x1173, 0x3162: 0x1174,
    0x3163: 0x1175,
}

# Compat Jamo → Hangul Jamo 종성 (Jongseong). 자음 일부와 겹받침.
_COMPAT_TO_JONG: Final[dict[int, int]] = {
    0x3131: 0x11A8,  # ㄱ
    0x3132: 0x11A9,  # ㄲ
    0x3133: 0x11AA,  # ㄳ
    0x3134: 0x11AB,  # ㄴ
    0x3135: 0x11AC,  # ㄵ
    0x3136: 0x11AD,  # ㄶ
    0x3137: 0x11AE,  # ㄷ
    0x3139: 0x11AF,  # ㄹ
    0x313A: 0x11B0,  # ㄺ
    0x313B: 0x11B1,  # ㄻ
    0x313C: 0x11B2,  # ㄼ
    0x313D: 0x11B3,  # ㄽ
    0x313E: 0x11B4,  # ㄾ
    0x313F: 0x11B5,  # ㄿ
    0x3140: 0x11B6,  # ㅀ
    0x3141: 0x11B7,  # ㅁ
    0x3142: 0x11B8,  # ㅂ
    0x3144: 0x11B9,  # ㅄ
    0x3145: 0x11BA,  # ㅅ
    0x3146: 0x11BB,  # ㅆ
    0x3147: 0x11BC,  # ㅇ
    0x3148: 0x11BD,  # ㅈ
    0x314A: 0x11BE,  # ㅊ
    0x314B: 0x11BF,  # ㅋ
    0x314C: 0x11C0,  # ㅌ
    0x314D: 0x11C1,  # ㅍ
    0x314E: 0x11C2,  # ㅎ
}


def _is_choseong(cp: int) -> bool:
    return _CHO_BASE <= cp <= _CHO_LAST


def _is_jungseong(cp: int) -> bool:
    return _JUNG_BASE <= cp <= _JUNG_LAST


def _is_jongseong(cp: int) -> bool:
    return _JONG_FIRST <= cp <= _JONG_LAST


def _is_syllable(cp: int) -> bool:
    return _SYLL_BASE <= cp < (_SYLL_BASE + _SYLL_COUNT)


def _decompose_syllable(cp: int) -> tuple[int, int, int]:
    """음절 → (초성, 중성, 종성) Hangul Jamo. 종성 없으면 0."""
    idx = cp - _SYLL_BASE
    cho = _CHO_BASE + idx // 588
    jung = _JUNG_BASE + (idx % 588) // 28
    jong_offset = idx % 28
    # offset 0 = 종성 없음, 1..27 = U+11A8 + (offset-1).
    jong = (_JONG_FIRST + jong_offset - 1) if jong_offset else 0
    return cho, jung, jong


def _make_syllable(cho: int, jung: int, jong: int = 0) -> str:
    """초성 + 중성 (+종성) → 음절 글자."""
    cho_idx = cho - _CHO_BASE
    jung_idx = jung - _JUNG_BASE
    # jong 가 0 (또는 falsy) 이면 종성 없음 (idx 0). 그 외엔 idx = cp - 0x11A8 + 1.
    jong_idx = (jong - _JONG_FIRST + 1) if jong else 0
    cp = _SYLL_BASE + cho_idx * 588 + jung_idx * 28 + jong_idx
    return chr(cp)


def compose_hangul(text: str) -> str:
    """자모 sequence 를 음절로 합성. ASCII / 한글 음절 / 그 외 unicode 통과.

    알고리즘 (state machine, greedy):
      1. 각 문자를 살펴봄. compat jamo 면 Hangul Jamo 로 매핑.
      2. 한글 음절을 만나면 그 음절을 (초성, 중성, 종성) 으로 분해 후
         새 진행 중인 음절 buffer 로 흡수 — 사용자가 이미 조합된 음절에
         뒤에 자모 추가하는 케이스도 처리.
      3. 다음 문자가 buffer 의 다음 위치 (cho → jung → jong) 와 맞으면 추가.
         안 맞으면 buffer flush 후 새 buffer 시작.
      4. 마지막에 buffer flush.

    예:
      'ㅎㅏㄴ'  → '한'
      'ㅎㅏㄴㄱㅜㄱ' → '한국'
      'ㅎㅏ' → '하'
      'ㅎ' → 'ㅎ' (자모 단독 — flush)
      'abc' → 'abc'
      '한ㄱㅜㄱ' → '한국' (이미 음절 + 자모 = 합성)
    """
    if not text:
        return ""

    out: list[str] = []
    cho: int | None = None
    jung: int | None = None
    jong: int | None = None

    def flush() -> None:
        """현재 buffer 의 자모를 (가능하면) 음절로 합쳐 out 에 push."""
        nonlocal cho, jung, jong
        if cho is None:
            cho = jung = jong = None
            return
        if jung is not None:
            out.append(_make_syllable(cho, jung, jong or 0))
        else:
            # 초성만 — 자모 단독으로 표시 (compat 으로 다시).
            out.append(_hangul_to_compat(cho))
        cho = jung = jong = None

    for ch in text:
        cp = ord(ch)

        # 1. 한글 음절이면 분해해 buffer 로 (= 사용자가 이미 조합된 음절
        #    뒤에 자모 추가하는 경우).
        if _is_syllable(cp):
            flush()
            cho, jung, jong_v = _decompose_syllable(cp)
            jong = jong_v or None
            continue

        # 2. Compat / Hangul Jamo 매핑.
        cho_cand: int | None = None
        jung_cand: int | None = None
        jong_cand: int | None = None
        if cp in _COMPAT_TO_CHO:
            cho_cand = _COMPAT_TO_CHO[cp]
        if cp in _COMPAT_TO_JUNG:
            jung_cand = _COMPAT_TO_JUNG[cp]
        if cp in _COMPAT_TO_JONG:
            jong_cand = _COMPAT_TO_JONG[cp]
        if _is_choseong(cp):
            cho_cand = cp
        if _is_jungseong(cp):
            jung_cand = cp
        if _is_jongseong(cp):
            jong_cand = cp

        # 자모 아니면 buffer flush 후 그대로 통과.
        if cho_cand is None and jung_cand is None and jong_cand is None:
            flush()
            out.append(ch)
            continue

        # 3. state machine.
        if cho is None:
            # 초성 자리.
            if cho_cand is not None:
                cho = cho_cand
            elif jung_cand is not None:
                # 중성 단독 — 자모 그대로 push.
                out.append(ch)
            else:
                out.append(ch)
        elif jung is None:
            # 중성 자리.
            if jung_cand is not None:
                jung = jung_cand
            elif cho_cand is not None:
                # 새 초성 — 기존 자모 (cho 단독) flush 후 새로.
                flush()
                cho = cho_cand
            else:
                # 종성만 가능한 자모 — 단독으로 push 불가, flush.
                flush()
                out.append(ch)
        elif jong is None:
            # 종성 자리 또는 새 음절 시작.
            if jong_cand is not None and cho_cand is not None:
                # 둘 다 후보 — greedy 로 종성에 일단 배치. 다음 글자가
                # 중성이면 retro fix: 마지막 자모는 다음 음절의 초성.
                # 단순화: 일단 종성으로 — 사용자 보고 케이스 (`ㅎㅏㄴ`)
                # 에 종성 자리. 다음 자모가 중성이면 jong → 다음 음절 cho
                # 로 옮김.
                jong = jong_cand
            elif cho_cand is not None:
                # 새 초성 — 현재 음절 flush 후 새 음절.
                flush()
                cho = cho_cand
            elif jong_cand is not None:
                jong = jong_cand
            elif jung_cand is not None:
                # 중성 다시 — 현재 음절 flush 후 그대로 (잘못된 sequence)?
                flush()
                out.append(ch)
            else:
                flush()
                out.append(ch)
        else:
            # 초/중/종 모두 채워짐 — 다음 글자는 새 음절 시작.
            if cho_cand is not None and jung_cand is None:
                # 종성 둘인 케이스 (겹받침은 단일 jong 자모로 처리, 별도
                # 매핑) — 일단 단순화: 새 음절 시작.
                flush()
                cho = cho_cand
            elif jung_cand is not None:
                # 이전 jong 이 사실 다음 음절의 cho — split.
                prev_jong = jong
                jong = None
                flush()
                # 이전 jong (Hangul Jamo) 를 cho 로 변환.
                if prev_jong is not None:
                    # jong → cho 매핑 (단순 겹받침 제외).
                    cho_from_jong = _jong_to_cho(prev_jong)
                    if cho_from_jong is not None:
                        cho = cho_from_jong
                        jung = jung_cand
                    else:
                        # 변환 안 됨 → 그냥 새 음절 (이미 flush 된 상태).
                        out.append(ch)
                else:
                    out.append(ch)
            elif jong_cand is not None:
                # 음절 끝났는데 또 종성 — 잘못된 sequence, flush.
                flush()
                out.append(ch)
            else:
                flush()
                out.append(ch)

    flush()
    return "".join(out)


def _hangul_to_compat(cp: int) -> str:
    """Hangul Jamo → Compat Jamo (사용자 표시용). 매핑 없으면 그대로."""
    rev = _hangul_to_compat_table()
    return chr(rev.get(cp, cp))


def _jong_to_cho(jong_cp: int) -> int | None:
    """종성 자모 → 초성 자모. 겹받침 등은 단순 매핑 안 됨 → None."""
    # 종성 ㄱ (U+11A8) → 초성 ㄱ (U+1100). 가장 단순한 자모만.
    table = {
        0x11A8: 0x1100,  # ㄱ
        0x11A9: 0x1101,  # ㄲ
        0x11AB: 0x1102,  # ㄴ
        0x11AE: 0x1103,  # ㄷ
        0x11AF: 0x1105,  # ㄹ
        0x11B7: 0x1106,  # ㅁ
        0x11B8: 0x1107,  # ㅂ
        0x11BA: 0x1109,  # ㅅ
        0x11BB: 0x110A,  # ㅆ
        0x11BC: 0x110B,  # ㅇ
        0x11BD: 0x110C,  # ㅈ
        0x11BE: 0x110E,  # ㅊ
        0x11BF: 0x110F,  # ㅋ
        0x11C0: 0x1110,  # ㅌ
        0x11C1: 0x1111,  # ㅍ
        0x11C2: 0x1112,  # ㅎ
    }
    return table.get(jong_cp)


_REV_TABLE: dict[int, int] | None = None


def _hangul_to_compat_table() -> dict[int, int]:
    """Hangul Jamo 초성 → Compat Jamo 역매핑 (lazy)."""
    global _REV_TABLE
    if _REV_TABLE is None:
        _REV_TABLE = {v: k for k, v in _COMPAT_TO_CHO.items()}
    return _REV_TABLE
