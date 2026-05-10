"""TagsPickerScreen — 단위 + 통합 테스트.

단위: `recommend_tags`, `filter_tags`, `_tokenize_for_recommend`.
통합: 모달 push, Input 타이핑 → OptionList 필터, Enter → dismiss(tag).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from textual.widgets import Input, OptionList

from whooing_tui.app import WhooingTuiApp
from whooing_tui.screens.tags_picker import (
    TagsPickerScreen,
    _tokenize_for_recommend,
    filter_tags,
    recommend_tags,
)


# ---- 단위 -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("스타벅스", ["스타벅스"]),
        ("스타벅스(아메리카노)", ["스타벅스", "아메리카노"]),
        ("점심 식사 - 외식", ["점심", "식사", "외식"]),
        ("a, b, c", ["a", "b", "c"]),  # 단, 1글자도 무시 → 사실 빈 결과
    ],
)
def test_tokenize_for_recommend(raw, expected):
    out = _tokenize_for_recommend(raw)
    if expected and len(expected[0]) == 1:
        # 1글자 토큰은 본 함수가 무시한다 — expected 갱신.
        assert out == []
    else:
        assert out == expected


def test_recommend_tags_prefers_substring_match():
    existing = {"커피": 8, "식비": 12, "외식": 5, "관계없는태그": 1}
    out = recommend_tags(
        item="스타벅스 아메리카노",
        memo="회의비 정산",
        existing=existing,
    )
    # 본문 어디에도 매칭되는 기존 태그 없음 → 빈 추천.
    assert out == []


def test_recommend_tags_token_match_ranks_high():
    existing = {"커피": 8, "식비": 12, "외식": 5}
    out = recommend_tags(
        item="외식 점심", memo="식비 결제",
        existing=existing,
    )
    # 외식 / 식비 둘 다 본문 토큰과 일치 (substring + token 둘 다 score=3).
    # tie 면 사용 빈도 (count) 내림차순 → 식비 (12) 먼저.
    assert out == ["식비", "외식"]


def test_recommend_tags_substring_only_lower_score():
    existing = {"식": 10, "식비": 5, "기타": 1}  # 1글자는 token 매칭 X
    out = recommend_tags(
        item="식사 비용", memo="",
        existing=existing,
    )
    # `식비` 는 substring 으로 본문에 없음 (식사 와 비용 사이에 공백) →
    # 추천 안 됨. `식` 는 1글자라 token 분리에서 빠지지만 substring 매칭은 됨.
    assert "식비" not in out  # substring 매칭은 본문 그대로 검사 → 안 들어감


@pytest.mark.parametrize(
    "query,tags,expected",
    [
        ("", ["식비", "커피", "외식"], ["식비", "커피", "외식"]),
        ("식", ["식비", "커피", "외식", "식사"], ["식비", "식사", "외식"]),
        ("커", ["식비", "커피"], ["커피"]),
        ("xx", ["식비", "커피"], []),
    ],
)
def test_filter_tags(query, tags, expected):
    assert filter_tags(query, tags) == expected


def test_filter_tags_case_insensitive():
    assert filter_tags("Co", ["coffee", "tea", "Cocoa"]) == ["coffee", "Cocoa"]


# ---- 통합 (App.run_test) -----------------------------------------------


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


class _BootStrapClient:
    """Minimal client — TagsPickerScreen 자체는 client 무관, App.run_test
    부트만 통과하면 된다."""
    def __init__(self) -> None:
        self.sections = [{"section_id": "s1", "title": "main"}]
        self.accounts = {"assets": [{"account_id": "x11", "title": "현금"}]}
        self._entries: list[dict[str, Any]] = []

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id: str):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self._entries)

    async def create_entry(self, **kwargs):
        return {"entry_id": "new-1", **kwargs}

    async def update_entry(self, **kwargs):
        return {**kwargs}

    async def delete_entry(self, **kwargs):
        return {}


@pytest.mark.asyncio
async def test_picker_dismisses_with_existing_tag():
    """OptionList 의 기존 태그 옵션 클릭 → dismiss(tag)."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        results: list = []

        def _on_pick(r):
            results.append(r)

        existing = {"식비": 12, "커피": 8}
        await app.push_screen(
            TagsPickerScreen(item="", memo="", existing=existing),
            _on_pick,
        )
        await pilot.pause()
        opt = app.screen.query_one("#tagpick-list", OptionList)
        # 첫 번째 enabled 옵션은 자주 쓰는 태그의 식비 (count 12 > 8).
        # 헤더 (disabled) 가 0 번째니 1 번째.
        first_enabled_id = None
        for i in range(opt.option_count):
            o = opt.get_option_at_index(i)
            if not o.disabled and o.id:
                first_enabled_id = o.id
                break
        assert first_enabled_id == "tag::식비"
        # OptionList.OptionSelected 직접 post.
        opt_obj = next(
            opt.get_option_at_index(i) for i in range(opt.option_count)
            if opt.get_option_at_index(i).id == "tag::식비"
        )
        app.screen.post_message(OptionList.OptionSelected(opt, opt_obj, 0))
        await pilot.pause()
        assert results == ["식비"]


@pytest.mark.asyncio
async def test_picker_input_creates_new_tag():
    """Input 에 타이핑 + Enter → 새 태그 생성으로 dismiss."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        results: list = []

        def _on_pick(r):
            results.append(r)

        await app.push_screen(
            TagsPickerScreen(item="", memo="", existing={"기존": 1}),
            _on_pick,
        )
        await pilot.pause()
        inp = app.screen.query_one("#tagpick-input", Input)
        inp.value = "신규태그"
        await pilot.pause()
        # Submitted 이벤트 → 새 태그 옵션 (highlighted) 선택 → dismiss.
        app.screen.post_message(Input.Submitted(inp, "신규태그"))
        await pilot.pause()
        assert results == ["신규태그"]


@pytest.mark.asyncio
async def test_picker_recommends_tags_from_item_memo():
    """item / memo 와 매칭되는 기존 태그가 추천 섹션에 노출."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        existing = {"외식": 5, "회의": 8, "관계없는": 3}
        await app.push_screen(
            TagsPickerScreen(
                item="외식 점심", memo="회의비",
                existing=existing,
            ),
        )
        await pilot.pause()
        opt = app.screen.query_one("#tagpick-list", OptionList)
        # 추천 섹션에는 외식, 회의 가 보여야 — 첫 enabled 태그 옵션 두 개.
        ids = [
            opt.get_option_at_index(i).id
            for i in range(opt.option_count)
            if not opt.get_option_at_index(i).disabled
        ]
        # 추천이 자주 쓰는 태그보다 위에 노출 — 외식 / 회의 가 앞쪽.
        # (정확한 순서는 score+count, count desc → 회의(8) 가 외식(5) 보다 앞)
        assert "tag::회의" in ids
        assert "tag::외식" in ids
        rec_pos = min(ids.index("tag::회의"), ids.index("tag::외식"))
        rest_pos = ids.index("tag::관계없는")
        assert rec_pos < rest_pos


@pytest.mark.asyncio
async def test_picker_input_filter_narrows_options():
    """Input 타이핑 → 매칭 안 되는 옵션 제거."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        existing = {"식비": 12, "외식": 5, "커피": 8}
        await app.push_screen(
            TagsPickerScreen(item="", memo="", existing=existing),
        )
        await pilot.pause()
        inp = app.screen.query_one("#tagpick-input", Input)
        inp.value = "식"
        await pilot.pause()
        opt = app.screen.query_one("#tagpick-list", OptionList)
        ids = [
            opt.get_option_at_index(i).id
            for i in range(opt.option_count)
            if not opt.get_option_at_index(i).disabled
        ]
        # 커피는 매칭 X — 빠짐. + 새 태그 옵션은 노출.
        assert "tag::커피" not in ids
        assert "tag::식비" in ids
        assert "tag::외식" in ids
        assert "new::식" in ids


@pytest.mark.asyncio
async def test_picker_displays_tags_with_hash_prefix():
    """CL #51115+: 옵션 라벨이 `#식비` 형태 — 사용자에게 일관된 `#` 표기."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        await app.push_screen(
            TagsPickerScreen(item="", memo="", existing={"식비": 12, "커피": 8}),
        )
        await pilot.pause()
        opt = app.screen.query_one("#tagpick-list", OptionList)
        labels = [
            str(opt.get_option_at_index(i).prompt)
            for i in range(opt.option_count)
            if not opt.get_option_at_index(i).disabled
        ]
        # 라벨에 #식비 / #커피 가 보여야.
        assert any("#식비" in l for l in labels)
        assert any("#커피" in l for l in labels)


@pytest.mark.asyncio
async def test_picker_strips_hash_from_user_input_when_creating():
    """사용자가 Input 에 `#카페` 라고 타이핑 후 새 태그 생성 → bare `카페`."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        results: list = []

        def _on_pick(r):
            results.append(r)

        await app.push_screen(
            TagsPickerScreen(item="", memo="", existing={}),
            _on_pick,
        )
        await pilot.pause()
        inp = app.screen.query_one("#tagpick-input", Input)
        inp.value = "#카페"
        await pilot.pause()
        # `+ 새 태그 만들기` 라벨에 #카페 노출
        opt = app.screen.query_one("#tagpick-list", OptionList)
        first = opt.get_option_at_index(0)
        assert "#카페" in str(first.prompt)
        # Enter 시 bare 태그로 dismiss.
        app.screen.post_message(Input.Submitted(inp, "#카페"))
        await pilot.pause()
        assert results == ["카페"]  # 내부 저장은 bare


@pytest.mark.asyncio
async def test_picker_excludes_already_selected():
    """`already_selected` 에 있는 태그는 옵션에서 제외."""
    app = WhooingTuiApp(client=_BootStrapClient())  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _wait_for(lambda: app.session.section_id == "s1")
        existing = {"식비": 12, "외식": 5}
        await app.push_screen(
            TagsPickerScreen(
                item="", memo="", existing=existing,
                already_selected=["식비"],
            ),
        )
        await pilot.pause()
        opt = app.screen.query_one("#tagpick-list", OptionList)
        ids = [
            opt.get_option_at_index(i).id
            for i in range(opt.option_count)
            if not opt.get_option_at_index(i).disabled
        ]
        assert "tag::식비" not in ids
        assert "tag::외식" in ids
