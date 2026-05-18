"""whooing_core.dupes — 중복 평가 휴리스틱 unit tests.

여러 입력 시나리오마다 verdict 등급과 keep_suggestion 이 합리적인지 확인.
"""

from __future__ import annotations

from whooing_core.dupes import (
    DupeReport,
    VERDICT_LABELS_KO,
    evaluate_duplicates,
    is_duplicate,
)


def _e(**kwargs):
    base = {
        "entry_id": "e1", "entry_date": "20260510",
        "money": 10000, "l_account_id": "x20", "r_account_id": "x11",
        "item": "스타벅스", "memo": "",
    }
    base.update(kwargs)
    return base


def test_identical_two_entries():
    rep = evaluate_duplicates([
        _e(entry_id="e1"),
        _e(entry_id="e2"),
    ])
    assert rep.verdict == "identical"
    assert is_duplicate(rep)
    assert rep.keep_suggestion == "e1"  # 동률 → 사전순.


def test_different_entries():
    rep = evaluate_duplicates([
        _e(entry_id="e1", money=10000, item="스타벅스"),
        _e(entry_id="e2", money=99999, item="버스",
           entry_date="20260101"),
    ])
    assert rep.verdict == "different"
    assert not is_duplicate(rep)


def test_swapped_accounts_same_money():
    """좌/우 계정만 반대 — 입출금 혼동 시나리오."""
    rep = evaluate_duplicates([
        _e(entry_id="a", l_account_id="x20", r_account_id="x11"),
        _e(entry_id="b", l_account_id="x11", r_account_id="x20"),
    ])
    assert rep.verdict == "very_likely"
    assert is_duplicate(rep)
    assert any("좌/우" in r for r in rep.reasons)


def test_item_whitespace_difference():
    """item 의 띄어쓰기/특수문자만 다른 경우."""
    rep = evaluate_duplicates([
        _e(entry_id="a", item="스타벅스 강남점"),
        _e(entry_id="b", item="스타벅스강남점"),
    ])
    assert rep.verdict == "very_likely"
    assert is_duplicate(rep)


def test_item_punctuation_difference():
    rep = evaluate_duplicates([
        _e(entry_id="a", item="GS25-방배점"),
        _e(entry_id="b", item="GS25 방배점"),
    ])
    assert rep.verdict == "very_likely"


def test_card_statement_vs_manual():
    """금액·날짜·계정 동일, item 만 다른 경우 — 카드 import + 수기 겹침."""
    rep = evaluate_duplicates([
        _e(entry_id="a", item="네이버페이 _ 스타벅스"),
        _e(entry_id="b", item="스타벅스"),
    ])
    assert rep.verdict == "very_likely"


def test_refund_sign_flip():
    """금액 부호만 반대 — 환불/취소 묶음."""
    rep = evaluate_duplicates([
        _e(entry_id="a", money=10000),
        _e(entry_id="b", money=-10000),
    ])
    assert rep.verdict == "very_likely"
    assert any("부호" in r for r in rep.reasons)


def test_near_date_same_money_possible():
    """금액 같고 날짜 1일 차 — possible."""
    rep = evaluate_duplicates([
        _e(entry_id="a", entry_date="20260510"),
        _e(entry_id="b", entry_date="20260511"),
    ])
    assert rep.verdict == "possible"
    assert not is_duplicate(rep)  # possible 은 사람 판단 필요.


def test_same_money_diff_accounts_possible():
    """금액·날짜 일치, 계정 완전 다름 — possible."""
    rep = evaluate_duplicates([
        _e(entry_id="a", l_account_id="x20", r_account_id="x11"),
        _e(entry_id="b", l_account_id="x21", r_account_id="x12"),
    ])
    assert rep.verdict == "possible"


def test_single_entry_returns_different():
    rep = evaluate_duplicates([_e(entry_id="solo")])
    assert rep.verdict == "different"
    assert rep.pairs == ()


def test_three_entries_pairs_all_calculated():
    rep = evaluate_duplicates([
        _e(entry_id="e1"),
        _e(entry_id="e2"),
        _e(entry_id="e3", money=99999, entry_date="20260101"),
    ])
    assert len(rep.pairs) == 3
    pair_ids = {(p[0], p[1]) for p in rep.pairs}
    assert pair_ids == {("e1", "e2"), ("e1", "e3"), ("e2", "e3")}
    assert rep.verdict == "identical"  # e1-e2 가 identical.


def test_keep_suggestion_prefers_older_date():
    rep = evaluate_duplicates([
        _e(entry_id="late", entry_date="20260520"),
        _e(entry_id="early", entry_date="20260510"),
    ])
    assert rep.keep_suggestion == "early"


def test_keep_suggestion_when_no_date():
    """entry_date 가 없으면 entry_id 사전순으로 fallback."""
    rep = evaluate_duplicates([
        _e(entry_id="z", entry_date=""),
        _e(entry_id="a", entry_date=""),
    ])
    assert rep.keep_suggestion == "a"


def test_verdict_labels_complete():
    """모든 verdict 값에 한국어 라벨이 있는지 (UI 가 빈칸 안 보이도록)."""
    for v in ("identical", "very_likely", "possible", "different"):
        assert VERDICT_LABELS_KO[v]


def test_dupe_report_is_frozen_dataclass():
    rep = evaluate_duplicates([_e(entry_id="a"), _e(entry_id="b")])
    assert isinstance(rep, DupeReport)
    # frozen — 수정 시도하면 FrozenInstanceError.
    try:
        rep.verdict = "different"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised
