"""ime.py 의 KOREAN_OF 매핑 + bind_ko helper 단위 테스트."""

from __future__ import annotations

import pytest

from whooing_tui.ime import KOREAN_OF, bind_ko


# ---- KOREAN_OF 매핑 ---------------------------------------------------


@pytest.mark.parametrize("en, ko", [
    ("q", "ㅂ"), ("w", "ㅈ"), ("e", "ㄷ"), ("r", "ㄱ"), ("t", "ㅅ"),
    ("a", "ㅁ"), ("s", "ㄴ"), ("d", "ㅇ"),
    ("y", "ㅛ"), ("n", "ㅜ"),
])
def test_korean_of_two_beolsik_mapping(en, ko):
    """우리가 단축키로 쓰는 키들의 두벌식 매핑이 정확한지."""
    assert KOREAN_OF[en] == ko


def test_korean_of_only_lowercase_ascii_keys():
    """대문자 / 숫자 / 특수기호 / 한글 자모 자체는 매핑에 없다."""
    for k in ("Q", "1", "?", "+", "ㅂ", "Q.", " "):
        assert k not in KOREAN_OF


# ---- bind_ko helper --------------------------------------------------


def test_bind_ko_returns_two_bindings_for_letter_key():
    bindings = bind_ko("q", "back", "Quit", show=True)
    assert len(bindings) == 2
    en_b, ko_b = bindings
    assert en_b.key == "q"
    assert en_b.action == "back"
    assert en_b.description == "Quit"
    assert en_b.show is True
    assert ko_b.key == "ㅂ"
    assert ko_b.action == "back"
    # 한글 binding 은 Footer 에 안 노출
    assert ko_b.show is False


def test_bind_ko_propagates_priority():
    """`priority=True` 같은 옵션이 한글 binding 에도 전달돼야 — 안 그러면
    OptionList / Tree focus 일 때 한글 키만 무시되는 비대칭 발생."""
    bindings = bind_ko("s", "open_sections", "Sections",
                       show=True, priority=True)
    assert len(bindings) == 2
    assert bindings[0].priority is True
    assert bindings[1].priority is True


def test_bind_ko_korean_always_priority(monkeypatch):
    """CL #51115+: 영문이 priority=False 이거나 미지정이라도 한글 binding
    은 항상 priority=True. focused widget 이 한글 자모를 텍스트로 흡수해
    화면에 잠시 표시되는 시각 지연을 막기 위해."""
    # 영문 priority 미지정 (default False)
    bindings = bind_ko("q", "back", "Quit", show=True)
    en_b, ko_b = bindings
    assert en_b.priority is False
    assert ko_b.priority is True  # 한글은 강제 priority

    # 영문이 priority=False 명시
    bindings = bind_ko("d", "delete", show=True, priority=False)
    en_b, ko_b = bindings
    assert en_b.priority is False
    assert ko_b.priority is True


def test_bind_ko_unmapped_key_returns_only_english():
    """매핑에 없는 키 (예: '?') 는 영문 binding 1개만."""
    bindings = bind_ko("question_mark", "help", "Help")
    assert len(bindings) == 1
    assert bindings[0].key == "question_mark"


def test_bind_ko_letter_with_no_description():
    """description 인자 생략 시도 OK (default 빈 문자열)."""
    bindings = bind_ko("a", "open_accounts")
    assert len(bindings) == 2
    assert bindings[0].description == ""
    assert bindings[1].description == ""


def test_bind_ko_show_false_propagates():
    """영문 binding 의 show=False 라면 한글도 그대로 (어차피 Footer X)."""
    bindings = bind_ko("d", "delete", show=False)
    assert bindings[0].show is False
    assert bindings[1].show is False


# ---- 통합: 한글 자모 키 입력이 textual key dispatch 에 도달하는지 ------


@pytest.mark.asyncio
async def test_korean_letter_binding_fires_via_pilot_press():
    """`pilot.press("ㅂ")` 이 `Binding("ㅂ", ...)` 의 action 을 fire 한다.

    textual 이 한글 자모 character 를 key event 로 받고 Binding 매칭에
    사용함을 확인 — 우리 `bind_ko` 패턴이 실 사용자의 한글 IME 입력에
    동작하는 근거.
    """
    from textual.app import App
    from textual.binding import Binding
    from textual.widgets import Static

    class Probe(App):
        BINDINGS = [
            Binding("q", "noop_en", "Q"),
            Binding("ㅂ", "noop_ko", "Ko"),
        ]
        last: str = ""

        def compose(self):
            yield Static("ok")

        def action_noop_en(self):
            self.last = "en"

        def action_noop_ko(self):
            self.last = "ko"

    app = Probe()
    async with app.run_test() as pilot:
        await pilot.press("q")
        assert app.last == "en"
        app.last = ""
        await pilot.press("ㅂ")
        assert app.last == "ko"


# ---- CL #51138+ (H8) 초성 분해 -----------------------------------------


def test_choseong_of_hangul():
    from whooing_tui.ime import choseong_of
    assert choseong_of("스") == "ㅅ"
    assert choseong_of("벅") == "ㅂ"
    assert choseong_of("가") == "ㄱ"
    assert choseong_of("힣") == "ㅎ"


def test_choseong_of_non_hangul_passthrough():
    from whooing_tui.ime import choseong_of
    assert choseong_of("A") == "A"
    assert choseong_of("1") == "1"
    assert choseong_of("!") == "!"
    assert choseong_of("") == ""


def test_to_choseong_string_korean_brand():
    from whooing_tui.ime import to_choseong_string
    assert to_choseong_string("스타벅스") == "ㅅㅌㅂㅅ"
    assert to_choseong_string("맥도날드") == "ㅁㄷㄴㄷ"
    assert to_choseong_string("카페") == "ㅋㅍ"


def test_to_choseong_string_mixed():
    from whooing_tui.ime import to_choseong_string
    assert to_choseong_string("한국T맵") == "ㅎㄱTㅁ"
    assert to_choseong_string("ABC") == "ABC"
    assert to_choseong_string("") == ""
