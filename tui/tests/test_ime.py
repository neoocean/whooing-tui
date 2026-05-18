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


# ---- CL #52720+ : IME 적용 누락 회귀 방지 -----------------------------


def _binding_keys(BINDINGS) -> list[str]:
    """BINDINGS 의 key string 만 추출 — Binding | str 양쪽 케이스."""
    out = []
    for b in BINDINGS:
        out.append(b.key if hasattr(b, "key") else b)
    return out


def test_app_q_t_have_korean_jamo_pair():
    """App-level Quit (q/ㅂ) + Theme (t/ㅅ) 가 IME 양쪽 매칭."""
    from whooing_tui.app import WhooingTuiApp
    keys = _binding_keys(WhooingTuiApp.BINDINGS)
    assert "q" in keys and "ㅂ" in keys, f"q/ㅂ missing in app: {keys}"
    assert "t" in keys and "ㅅ" in keys, f"t/ㅅ missing in app: {keys}"


def test_attachment_browser_letter_keys_have_korean_jamo_pair():
    """AttachmentBrowser 의 a/d/o/e/r 모두 한글 자모와 짝."""
    from whooing_tui.screens.attachment_browser import AttachmentBrowserScreen
    keys = _binding_keys(AttachmentBrowserScreen.BINDINGS)
    for en, ko in [("a", "ㅁ"), ("d", "ㅇ"), ("o", "ㅐ"),
                   ("e", "ㄷ"), ("r", "ㄱ")]:
        assert en in keys, f"{en} missing"
        assert ko in keys, f"{ko} (for {en}) missing"


def test_confirm_modal_yn_have_korean_jamo_pair():
    """ConfirmModal (통합 widget) 의 y/n 도 IME 양쪽."""
    from whooing_tui.widgets.confirm import ConfirmModal
    keys = _binding_keys(ConfirmModal.BINDINGS)
    assert "y" in keys and "ㅛ" in keys
    assert "n" in keys and "ㅜ" in keys


def test_dashboard_r_has_korean_jamo_pair():
    """DashboardScreen 의 r (refresh) 도 IME 매칭."""
    from whooing_tui.screens.dashboard import DashboardScreen
    keys = _binding_keys(DashboardScreen.BINDINGS)
    assert "r" in keys and "ㄱ" in keys


@pytest.mark.asyncio
async def test_app_quit_actually_fires_on_korean_jamo():
    """`pilot.press('ㅂ')` 이 실제로 App.action_quit 을 발사 — 사용자 보고
    회귀 (q 만 작동, ㅂ 안 됨) 가 다시 발생하지 않도록 통합 검증."""
    from whooing_tui.app import WhooingTuiApp

    app = WhooingTuiApp(client=None)
    async with app.run_test() as pilot:
        # WhooingTuiApp.on_mount 가 client=None 케이스에서는 화면 push 안
        # 함 — 기본 Footer/Header 만 보이는 상태. 이 상태에서도 BINDINGS 의
        # quit 가 매칭돼야 한다.
        await pilot.press("ㅂ")
        # quit action 이 발사되면 app 의 _exit flag 가 set — return_code 가
        # 0 으로 설정됨. textual 8.x 의 정확한 종료 표면은 환경 의존이라
        # exit 만 확인해도 회귀 방지에 충분.
        await pilot.pause()
        assert app._return_value is None and app._exit, (
            "ㅂ key did not trigger app quit — IME regression"
        )


# ---- CL #52761+ : graceful quit modal --------------------------------


def test_q_binding_action_is_graceful_quit_not_quit():
    """q / ㅂ 의 action 이 `graceful_quit` 으로 변경 — `_ShutdownModal` 진입.

    종전엔 `quit` (즉시 exit) — cli 가 응답 없는 상태로 잠깐 멈춰서 사용자
    가 ctrl+c 로 중단 시도하면 작업 누락. 이제 TUI 안에서 모달 표시 →
    완료 후 exit.
    """
    from whooing_tui.app import WhooingTuiApp
    actions = {b.key: b.action for b in WhooingTuiApp.BINDINGS}
    assert actions["q"] == "graceful_quit"
    assert actions["ㅂ"] == "graceful_quit"
    # ctrl+c 는 강제 종료 path 그대로.
    assert actions["ctrl+c"] == "quit"


def test_shutdown_modal_class_exists():
    """`_ShutdownModal` 이 ModalScreen 으로 정의 — 화면 가운데 모달."""
    from whooing_tui.app import _ShutdownModal
    from textual.screen import ModalScreen
    assert issubclass(_ShutdownModal, ModalScreen)


@pytest.mark.asyncio
async def test_graceful_quit_pushes_shutdown_modal():
    """q 누르면 `_ShutdownModal` 이 먼저 push — 사용자에게 "종료 중" 표시.

    worker 가 곧 self.exit() 를 호출해 결국 종료되지만, modal 이 한 번이
    라도 보이는지 검증 (사용자 명시: "종료 중 팝업").
    """
    from whooing_tui.app import WhooingTuiApp, _ShutdownModal

    app = WhooingTuiApp(client=None)
    pushed: list[type] = []

    async with app.run_test() as pilot:
        # push_screen 을 monkey-style wrap 으로 추적.
        original_push = app.push_screen

        def _track_push(screen, *args, **kwargs):
            pushed.append(type(screen))
            return original_push(screen, *args, **kwargs)

        app.push_screen = _track_push  # type: ignore[assignment]
        # action_graceful_quit 직접 호출 (pilot.press 는 race 가능).
        app.action_graceful_quit()
        await pilot.pause()
        assert _ShutdownModal in pushed, (
            f"_ShutdownModal push 안 됨. pushed types: {pushed}"
        )


@pytest.mark.asyncio
async def test_graceful_quit_double_press_is_idempotent():
    """이미 _ShutdownModal 이 떠 있는 상태에서 q 다시 눌러도 중복 push X.

    사용자가 종료 중 화면을 보고 q 를 또 눌러도 새 modal 안 쌓이고 worker
    도 새로 안 띄움 (`@work(exclusive=True, group="shutdown")` 으로 보장).
    """
    from whooing_tui.app import WhooingTuiApp, _ShutdownModal

    app = WhooingTuiApp(client=None)
    pushed_modals: list[type] = []

    async with app.run_test() as pilot:
        original_push = app.push_screen

        def _track_push(screen, *args, **kwargs):
            pushed_modals.append(type(screen))
            return original_push(screen, *args, **kwargs)

        app.push_screen = _track_push  # type: ignore[assignment]
        app.action_graceful_quit()
        await pilot.pause()
        # 두 번째 호출 — 이미 modal 떠 있어 noop.
        before = pushed_modals.count(_ShutdownModal)
        app.action_graceful_quit()
        await pilot.pause()
        after = pushed_modals.count(_ShutdownModal)
        assert after == before, (
            f"중복 push 발생: before={before} after={after}"
        )
