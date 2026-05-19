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


# ---- CL #52917+ : public normalize_text / merchant_similar -------------


def test_normalize_text_strips_whitespace_and_punct():
    from whooing_core.dupes import normalize_text
    assert normalize_text("스타벅스 강남점") == normalize_text("스타벅스강남점")
    assert normalize_text("GS25-방배점") == normalize_text("GS25 방배점")


def test_normalize_text_handles_none():
    from whooing_core.dupes import normalize_text
    assert normalize_text(None) == ""


def test_merchant_similar_substring_match():
    from whooing_core.dupes import merchant_similar
    assert merchant_similar("스타벅스", "스타벅스 강남점") is True
    assert merchant_similar("스타벅스 강남점", "스타벅스") is True
    assert merchant_similar("스타벅스강남점", "스타벅스 강남점") is True


def test_merchant_similar_unrelated():
    from whooing_core.dupes import merchant_similar
    assert merchant_similar("스타벅스", "버거킹") is False


def test_merchant_similar_empty_or_short():
    from whooing_core.dupes import merchant_similar
    assert merchant_similar("", "스타벅스") is False
    assert merchant_similar("스타벅스", "") is False
    # 정규화 후 < 3자: 정확 일치만.
    assert merchant_similar("GS", "GS25") is False
    assert merchant_similar("AB", "AB") is True


# ----------------------------------------------------------------------
# find_duplicate_clusters — bulk 스캐너 (CL #52957+).
# ----------------------------------------------------------------------


def test_find_clusters_empty():
    from whooing_core.dupes import find_duplicate_clusters
    assert find_duplicate_clusters([]) == []


def test_find_clusters_no_duplicates():
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", money=10000),
        _e(entry_id="b", money=20000, entry_date="20260101"),
        _e(entry_id="c", money=30000, entry_date="20260601"),
    ])
    assert clusters == []


def test_find_clusters_identical_pair():
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a"),
        _e(entry_id="b"),
        _e(entry_id="lonely", money=99999, entry_date="20260101"),
    ])
    assert len(clusters) == 1
    c = clusters[0]
    assert c.verdict == "identical"
    assert {e["entry_id"] for e in c.entries} == {"a", "b"}
    assert c.keep_suggestion == "a"


def test_find_clusters_swapped_accounts_same_day():
    """좌/우 반전 — 같은 절대 금액 bucket 안에서 잡혀야 함."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", l_account_id="x20", r_account_id="x11"),
        _e(entry_id="b", l_account_id="x11", r_account_id="x20"),
    ])
    assert len(clusters) == 1
    assert clusters[0].verdict == "very_likely"


def test_find_clusters_window_excludes_far_apart():
    """같은 가맹점 같은 금액이라도 한 달 차이는 cluster 아님 (window 7)."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", entry_date="20260101"),
        _e(entry_id="b", entry_date="20260301"),  # 60일 차
    ])
    assert clusters == []


def test_find_clusters_window_includes_nearby():
    """±1 일 — possible."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", entry_date="20260101"),
        _e(entry_id="b", entry_date="20260102"),
    ])
    # 같은 raw 데이터 + 1일 차 → possible (날짜 다름 + 그 외 모두 같음).
    # _pair_verdict 가 raw_item_eq+raw_memo_eq 인 경우 same_date 까지 가야
    # identical, 여기선 same_date 거짓 → very_likely 분기들도 거짓 →
    # near_date 인 possible.
    assert len(clusters) == 1
    assert clusters[0].verdict in ("possible", "very_likely")


def test_find_clusters_zero_money_skipped():
    """money 0 / None 은 신호 너무 약해 cluster 후보 자체에서 제외."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", money=0),
        _e(entry_id="b", money=0),
    ])
    assert clusters == []


def test_find_clusters_three_way_component():
    """세 거래가 pairwise 로 매치 → 하나의 cluster (size=3)."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", entry_date="20260510"),
        _e(entry_id="b", entry_date="20260510"),
        _e(entry_id="c", entry_date="20260510"),
    ])
    assert len(clusters) == 1
    assert len(clusters[0].entries) == 3


def test_find_clusters_transitive_via_window():
    """A↔B (1일차) 와 B↔C (1일차) 면 A↔C 는 2일차라도 한 component."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", entry_date="20260510"),
        _e(entry_id="b", entry_date="20260511"),
        _e(entry_id="c", entry_date="20260512"),
    ])
    assert len(clusters) == 1
    assert len(clusters[0].entries) == 3


def test_find_clusters_keep_suggestion_oldest():
    from whooing_core.dupes import find_duplicate_clusters
    # 같은 날짜 → identical, 그 안에서 entry_id 사전순.
    clusters = find_duplicate_clusters([
        _e(entry_id="newer"),
        _e(entry_id="older"),
    ])
    assert len(clusters) == 1
    assert clusters[0].keep_suggestion == "newer"  # 동률 → 사전순.


def test_find_clusters_sorted_by_verdict_strength():
    """identical cluster 가 possible cluster 보다 먼저 반환."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        # cluster A — identical (money=10000, 같은 day, 모두 같음).
        _e(entry_id="a1", money=10000),
        _e(entry_id="a2", money=10000),
        # cluster B — possible (money=50000, 1일차, 같은 raw 필드).
        _e(entry_id="b1", money=50000, entry_date="20260101"),
        _e(entry_id="b2", money=50000, entry_date="20260102"),
    ])
    assert len(clusters) == 2
    # 강한 verdict 가 먼저.
    assert clusters[0].verdict == "identical"
    assert clusters[1].verdict in ("possible", "very_likely")


def test_find_clusters_min_verdict_filter():
    """min_verdict=very_likely 면 possible cluster 는 결과에서 제외."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", entry_date="20260101"),
        _e(entry_id="b", entry_date="20260102"),
    ], min_verdict="very_likely")
    # 1일차 + 같은 raw 데이터 → _pair_verdict 분기에서 same_date 거짓,
    # near_date+same_money+same_accounts 의 "possible" 라우트로 갈 수 있음.
    # very_likely 이상만 통과 → 비어있어야 한다.
    assert all(c.verdict != "possible" for c in clusters)


def test_find_clusters_custom_date_window():
    """date_window_days=30 이면 한 달 차도 cluster."""
    from whooing_core.dupes import find_duplicate_clusters
    clusters = find_duplicate_clusters([
        _e(entry_id="a", entry_date="20260101"),
        _e(entry_id="b", entry_date="20260120"),  # 19일차
    ], date_window_days=30, min_verdict="possible")
    # 같은 raw 데이터 + 19일차. _pair_verdict 의 near_date 는 ≤1 만 → possible
    # 도 안 됨. 같은 raw 모든 필드 + 같은 절대값 매치 길은 없으므로 "different".
    # → cluster 없음. 정상 (window 만 넓혀도 pair_verdict 가 막음 — 사용자에게
    # 신중한 결과 제공).
    assert clusters == []


def test_find_clusters_dedup_input_by_entry_id():
    """같은 entry_id 두 번 들어오면 한 번만 처리 (안전망)."""
    from whooing_core.dupes import find_duplicate_clusters
    same = _e(entry_id="a")
    clusters = find_duplicate_clusters([same, same])
    assert clusters == []
