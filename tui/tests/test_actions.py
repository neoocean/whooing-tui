"""`actions.safe_action` 데코레이터 unit tests.

CL #52834+. 보일러플레이트 try/except 를 제거하는 decorator. 사용자 가시
status 메시지의 형식을 검증.
"""

from __future__ import annotations

import asyncio

import pytest

from whooing_tui.actions import safe_action
from whooing_tui.models import ToolError


class _StubScreen:
    """set_status(msg, error=True|False) 인터페이스를 가진 최소 stub."""

    def __init__(self) -> None:
        self.last_msg: str = ""
        self.last_error: bool = False
        self.set_status_calls: int = 0

    def set_status(self, msg: str, *, error: bool = False) -> None:
        self.last_msg = msg
        self.last_error = error
        self.set_status_calls += 1


# ---- 동기 함수 ---------------------------------------------------------


def test_safe_action_sync_returns_value_when_no_error():
    """정상 종료면 wrapped 의 반환값이 그대로."""
    screen = _StubScreen()

    @safe_action("작업")
    def do(self_, x):
        return x * 2

    result = do(screen, 5)
    assert result == 10
    # 정상 종료엔 status 를 만지지 않는다.
    assert screen.set_status_calls == 0


def test_safe_action_sync_handles_tool_error():
    screen = _StubScreen()

    @safe_action("거래 생성")
    def do(self_):
        raise ToolError("USER_INPUT", "잘못된 파라미터")

    do(screen)
    assert screen.last_error is True
    assert "거래 생성 실패" in screen.last_msg
    assert "USER_INPUT" in screen.last_msg
    assert "잘못된 파라미터" in screen.last_msg


def test_safe_action_sync_handles_generic_exception():
    screen = _StubScreen()

    @safe_action("거래 삭제")
    def do(self_):
        raise RuntimeError("DB 잠김")

    do(screen)
    assert screen.last_error is True
    assert "INTERNAL" in screen.last_msg
    assert "DB 잠김" in screen.last_msg


def test_safe_action_uses_func_name_when_label_omitted():
    screen = _StubScreen()

    @safe_action()
    def my_named_action(self_):
        raise RuntimeError("X")

    my_named_action(screen)
    assert "my_named_action 실패" in screen.last_msg


# ---- 비동기 함수 -------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_action_async_returns_value():
    screen = _StubScreen()

    @safe_action("비동기")
    async def do(self_, x):
        return x + 1

    assert await do(screen, 4) == 5
    assert screen.set_status_calls == 0


@pytest.mark.asyncio
async def test_safe_action_async_handles_tool_error():
    screen = _StubScreen()

    @safe_action("비동기 호출")
    async def do(self_):
        await asyncio.sleep(0)
        raise ToolError("INTERNAL", "서버 5xx")

    await do(screen)
    assert screen.last_error is True
    assert "비동기 호출 실패" in screen.last_msg
    assert "INTERNAL" in screen.last_msg


@pytest.mark.asyncio
async def test_safe_action_async_handles_generic_exception():
    screen = _StubScreen()

    @safe_action("작업")
    async def do(self_):
        raise ValueError("bad data")

    await do(screen)
    assert "INTERNAL" in screen.last_msg
    assert "bad data" in screen.last_msg


# ---- target 이 set_status 없는 경우 -----------------------------------


def test_safe_action_target_without_set_status_no_crash():
    """set_status 없는 target — silent. raise 하지 않아야."""

    class _NoStatus:
        pass

    @safe_action("작업")
    def do(self_):
        raise RuntimeError("X")

    # 예외 propagate X.
    do(_NoStatus())  # 통과만 하면 ok.


# ---- functools.wraps 정상 동작 -----------------------------------------


def test_safe_action_preserves_name_and_doc():
    @safe_action("X")
    def my_action(self_):
        """원본 docstring."""
        return None

    assert my_action.__name__ == "my_action"
    assert my_action.__doc__ == "원본 docstring."
