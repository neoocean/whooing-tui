"""거래내역 중복 감지 — 여러 휴리스틱을 묶은 평가.

사용자 요청 (CL #52815+):
> 둘 이상의 거래내역을 선택한 다음 컨텍스트메뉴에서 '중복인지 평가' —
> 중복이라도 여러 가지 방식으로 다르게 입력되어 있을 수 있으니 다양한
> 방법으로 평가해야 한다.

중복 입력은 흔히 다음 같은 모양으로 갈린다:
  - 같은 거래를 두 번 등록 — 동일 (eid 만 다름).
  - 금액 같지만 좌우 계정이 *반대* 로 입력됨 (입출금 헷갈림).
  - item 의 띄어쓰기 / 특수문자 / 공백만 다름 — "스타벅스 강남점" vs "스타벅스강남점".
  - 카드 명세서 import 와 수기 입력이 겹침 — memo 만 다르고 item 은 동일.
  - 부분 환불 등 금액 부호가 반대 (음수 vs 양수).
  - 날짜가 하루 차이 (가맹점 처리 지연).

본 모듈은 pure function — sqlite / 후잉 의존 없음. 입력은 entry dict
list, 출력은 (등급, 사유, 매칭쌍) 형태의 보고서.

평가 결과는 4 단계 등급:
  - "identical"     — 모든 핵심 필드 일치. 안전하게 dedup 가능.
  - "very_likely"   — 1~2 개 필드만 다르고 의미상 같은 거래 (반대 계정 /
                      memo 차이 등). 사용자 확인 후 dedup.
  - "possible"      — 금액 + 날짜만 일치. 우연일 수 있어 신중 검토.
  - "different"    — 어느 휴리스틱도 통과 못함. 별개 거래.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Literal

Verdict = Literal["identical", "very_likely", "possible", "different"]


@dataclass(frozen=True)
class DupeReport:
    """중복 평가 결과.

    verdict: 가장 강한 매칭 등급.
    reasons: 매칭에 동원된 휴리스틱 이름 list (사용자에게 보여주는 해설).
    pairs: 매칭된 entry_id 쌍 list (i < j, 모든 쌍 — n*(n-1)/2 최대).
           각 쌍의 verdict 도 함께 (모두 같은 등급은 아닐 수 있다).
    keep_suggestion: 사용자가 "하나만 남기기" 선택 시 기본으로 권장할
                     entry_id. 보통 가장 오래된 (entry_id 사전순 작은) 것.
    """

    verdict: Verdict
    reasons: tuple[str, ...]
    pairs: tuple[tuple[str, str, Verdict, tuple[str, ...]], ...]
    keep_suggestion: str | None


def _strip_money(v: Any) -> int | None:
    """money 필드 → 절대값 (반대 부호 매칭용). None / 빈문자 / 비숫자는 None."""
    if v is None or v == "":
        return None
    try:
        return abs(int(v))
    except (TypeError, ValueError):
        try:
            return abs(int(float(v)))
        except (TypeError, ValueError):
            return None


def _signed_money(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\wÀ-￿]+")


def _norm_text(s: Any) -> str:
    """item / memo 정규화 — NFKC + 공백 제거 + 구두점 제거 + lowercase.

    "스타벅스 강남점" 과 "스타벅스강남점" 이 같아지도록. 한글 자모는 NFKC
    가 음절로 모아주므로 IME 차이도 흡수.
    """
    if s is None:
        return ""
    t = unicodedata.normalize("NFKC", str(s))
    t = t.casefold()
    t = _SPACE_RE.sub("", t)
    t = _PUNCT_RE.sub("", t)
    return t


# CL #52917+: public alias — caller 가 직접 정규화 비교에 사용 가능.
normalize_text = _norm_text


def merchant_similar(a: str, b: str) -> bool:
    """두 가맹점 문자열이 정규화 후 *유사* 한지 — substring 매칭.

    "스타벅스" 와 "스타벅스 강남점" 같이 한 쪽이 다른 쪽을 포함하면 True.
    너무 짧은 (정규화 후 3자 미만) 문자열은 false positive 위험으로 정확
    일치만 인정.

    CL #52917+ — 카드 명세서 import 의 fuzzy dedup 용. 정규화는 NFKC +
    casefold + 공백 / 구두점 제거 → 같은 가맹점이 명세서마다 다른 표기로
    들어와도 매칭.
    """
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return False
    if len(na) < 3 or len(nb) < 3:
        return na == nb
    return na in nb or nb in na


def _date(v: Any) -> str:
    """entry_date — YYYYMMDD 문자열로. 다른 표기는 그대로 (비교만)."""
    if v is None:
        return ""
    return str(v).strip()


def _day_diff(a: str, b: str) -> int | None:
    """두 YYYYMMDD 의 일수 차 — 잘못된 입력은 None."""
    if len(a) < 8 or len(b) < 8 or not a[:8].isdigit() or not b[:8].isdigit():
        return None
    try:
        from datetime import datetime
        da = datetime.strptime(a[:8], "%Y%m%d")
        db = datetime.strptime(b[:8], "%Y%m%d")
        return abs((da - db).days)
    except ValueError:  # pragma: no cover — already digit-checked
        return None


def _pair_verdict(
    a: dict[str, Any], b: dict[str, Any],
) -> tuple[Verdict, tuple[str, ...]]:
    """두 entry 의 매칭 등급 + 그 이유.

    체크 순서는 강한 매칭부터 (식별되면 바로 반환):
      1. identical — 핵심 필드 모두 동일.
      2. very_likely — 좌우 반대 / item 정규화 일치 / memo 만 다름 등.
      3. possible — 금액 같고 날짜 ±1 이내.
      4. different.
    """
    a_money = _signed_money(a.get("money"))
    b_money = _signed_money(b.get("money"))
    a_abs = _strip_money(a.get("money"))
    b_abs = _strip_money(b.get("money"))
    a_date = _date(a.get("entry_date"))
    b_date = _date(b.get("entry_date"))
    a_l = str(a.get("l_account_id") or "")
    a_r = str(a.get("r_account_id") or "")
    b_l = str(b.get("l_account_id") or "")
    b_r = str(b.get("r_account_id") or "")
    a_item = _norm_text(a.get("item"))
    b_item = _norm_text(b.get("item"))
    a_memo = _norm_text(a.get("memo"))
    b_memo = _norm_text(b.get("memo"))

    reasons: list[str] = []

    # 1. identical — 모든 raw 필드 byte-exact. 정규화 후만 같은 경우는
    # 사용자가 raw 입력의 차이를 인지할 수 있도록 very_likely 로 떨군다.
    raw_item_eq = (a.get("item") or "") == (b.get("item") or "")
    raw_memo_eq = (a.get("memo") or "") == (b.get("memo") or "")
    if (
        a_money is not None and a_money == b_money
        and a_date and a_date == b_date
        and a_l == b_l and a_r == b_r
        and raw_item_eq and raw_memo_eq
    ):
        reasons.append("모든 핵심 필드 일치")
        return ("identical", tuple(reasons))

    # 2. very_likely — 의미상 같은 거래.
    same_money = a_money is not None and a_money == b_money
    same_abs_money = (
        a_abs is not None and a_abs == b_abs and a_money != b_money
    )
    same_date = bool(a_date) and a_date == b_date
    near_date = False
    if not same_date:
        d = _day_diff(a_date, b_date)
        near_date = d is not None and d <= 1

    same_accounts = a_l == b_l and a_r == b_r
    swapped_accounts = a_l == b_r and a_r == b_l and a_l != a_r

    # 좌우 반대 + 금액 (절대값) 일치 + 같은 날 → 매우 가능성.
    if same_date and swapped_accounts and (same_money or same_abs_money):
        reasons.append("좌/우 계정이 반대 — 입출금 혼동 가능")
        if same_abs_money and not same_money:
            reasons.append("금액 부호 반대 (환불/취소 가능)")
        return ("very_likely", tuple(reasons))

    # item 정규화 일치 + 금액/날짜 일치 → 매우 가능성 (띄어쓰기/특수문자만 차이).
    if (
        same_money and same_date and same_accounts
        and a_item and a_item == b_item
        and (a.get("item") or "") != (b.get("item") or "")
    ):
        reasons.append("item 의 공백/특수문자 차이만 있음")
        return ("very_likely", tuple(reasons))

    # 금액 + 날짜 + 좌우 계정 일치, item 다르고 memo 도 다름 →
    # 카드 명세서 + 수기 입력 겹침 가능.
    if same_money and same_date and same_accounts and a_item and b_item:
        reasons.append("금액·날짜·계정 일치 (item 만 다름) — 이중 입력 가능")
        return ("very_likely", tuple(reasons))

    # 금액 절대값 + 날짜 + 좌우 계정 일치, 부호만 반대 → 환불/취소 묶음.
    if same_abs_money and same_date and same_accounts:
        reasons.append("금액 부호만 반대 (환불/취소 묶음 가능)")
        return ("very_likely", tuple(reasons))

    # 3. possible — 금액 일치 + 날짜 ±1.
    if (same_money or same_abs_money) and near_date:
        reasons.append("금액 같고 날짜 1일 이내 — 가맹점 처리 지연 가능")
        if same_accounts:
            reasons.append("좌/우 계정도 일치")
        elif swapped_accounts:
            reasons.append("좌/우 계정은 반대")
        return ("possible", tuple(reasons))

    # 금액 일치 + 같은 날 + 계정 다름 → 우연일 수도, 분할일 수도.
    if same_money and same_date:
        reasons.append("금액·날짜 일치 (계정 다름)")
        return ("possible", tuple(reasons))

    return ("different", tuple(reasons))


_VERDICT_ORDER: dict[Verdict, int] = {
    "different": 0,
    "possible": 1,
    "very_likely": 2,
    "identical": 3,
}


def evaluate_duplicates(entries: Iterable[dict[str, Any]]) -> DupeReport:
    """selection 된 entry list 의 중복 평가.

    n=1 은 호출자가 미리 막아야 하지만 안전하게 "different" 반환.
    n>=2 는 모든 쌍 (i,j) 의 verdict 를 계산해 가장 강한 등급으로 결론.

    keep_suggestion 은 다음 우선순위:
      1. entry_date 가장 오래된 것 (먼저 기록된 거래 유지).
      2. 동률이면 entry_id 사전순 가장 작은 것.
    """
    items = list(entries)
    if len(items) < 2:
        return DupeReport(
            verdict="different", reasons=(), pairs=(), keep_suggestion=None,
        )

    pairs: list[tuple[str, str, Verdict, tuple[str, ...]]] = []
    strongest: Verdict = "different"
    strongest_reasons: tuple[str, ...] = ()
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a = items[i]
            b = items[j]
            aid = str(a.get("entry_id") or "")
            bid = str(b.get("entry_id") or "")
            v, r = _pair_verdict(a, b)
            pairs.append((aid, bid, v, r))
            if _VERDICT_ORDER[v] > _VERDICT_ORDER[strongest]:
                strongest = v
                strongest_reasons = r

    def _keep_key(e: dict[str, Any]) -> tuple[str, str]:
        return (_date(e.get("entry_date")), str(e.get("entry_id") or ""))

    keep = min(items, key=_keep_key)
    keep_id = str(keep.get("entry_id") or "") or None

    return DupeReport(
        verdict=strongest,
        reasons=strongest_reasons,
        pairs=tuple(pairs),
        keep_suggestion=keep_id,
    )


def is_duplicate(report: DupeReport) -> bool:
    """UI 가 "중복인가?" 단순 boolean 으로 판단할 때.

    "possible" 은 사람의 판단이 필요해 *중복* 으로 보지 않는다. 사용자가
    DupeReport 의 pairs 를 직접 살펴봐야.
    """
    return report.verdict in ("identical", "very_likely")


VERDICT_LABELS_KO: dict[Verdict, str] = {
    "identical": "동일 거래",
    "very_likely": "중복 매우 유력",
    "possible": "중복 가능성 있음",
    "different": "중복 아님",
}


# ----------------------------------------------------------------------
# Bulk 스캐너 (CL #52963+).
# ----------------------------------------------------------------------
#
# 사용자 요청 (2026-05-19):
#   "거래내력에 중복으로 보이는 항목들이 많이 생겼습니다. … 지난 3년 동안의
#   거래를 검사해 중복인 항목들을 하나씩 보여주고 삭제할 것과 남길 것을
#   선택해 엔터를 누르면 중복이 처리되도록 해 주세요. … 중복 감지는 날짜,
#   왼쪽과 오른쪽, 메모, 금액 중 일부가 일치하되 날짜 범위가 너무 큰 차이
#   나지 않는 것을 기본으로 합니다."
#
# `evaluate_duplicates` 는 **선택된 소수** 에 대한 pairwise 평가 — 수천 건
# 위에 그대로 돌리면 O(n²). 본 모듈은 1단계 bucket (절대값 금액) + 2단계
# 윈도우 (날짜 ±date_window_days) 로 후보 쌍을 좁힌 뒤, 기존
# `_pair_verdict` 를 재사용해 connected component 클러스터링.
#
# 알고리즘:
#   1. 절대값 금액으로 bucket — 카드 명세서 부분 환불, 좌우 반전 모두 같은
#      bucket 안. 같은 절대값 같은 부호도 묶음. money 가 None / 0 이면 skip.
#   2. bucket 안에서 entry_date 기준 정렬, two-pointer 로 ±window 안 쌍만
#      추출 — 1년 같은 가맹점 같은 금액 거래에서 폭증 회피.
#   3. 각 후보 쌍에 `_pair_verdict` 호출, `different` 가 아니면 union-find.
#   4. component 크기 ≥ 2 만 cluster 로 반환, verdict 강한 순 정렬.
#
# bucket 안 entry 수 N 이라도 윈도우 안 평균 k 개 (k « N) 면 O(N·k) 수렴.
# 3년치 진짜 케이스 (~5000 건, 평균 1~3개 동금액 윈도우 안) 도 1~2초 내.


def _verdict_strength(v: Verdict) -> int:
    return _VERDICT_ORDER[v]


@dataclass(frozen=True)
class DupeCluster:
    """중복 후보 1 cluster — 2건 이상의 entry + 가장 강한 verdict.

    entries: cluster 안 모든 entry (입력된 dict 그대로). 최소 2 건.
    verdict: cluster 안 가장 강한 pair verdict (identical > very_likely > possible).
    reasons: 그 강한 pair 의 이유 list.
    keep_suggestion: 사용자가 '하나만 남기기' 선택 시 권장 entry_id —
                     entry_date 가장 오래된 것 (먼저 기록).
    """

    entries: tuple[dict[str, Any], ...]
    verdict: Verdict
    reasons: tuple[str, ...]
    keep_suggestion: str | None


def find_duplicate_clusters(
    entries: Iterable[dict[str, Any]],
    *,
    date_window_days: int = 7,
    min_verdict: Verdict = "possible",
) -> list[DupeCluster]:
    """전체 entry list 에서 중복 의심 cluster 들을 찾아 반환.

    Args:
        entries: 후잉 entries-list 반환 dict 의 iterable. money / entry_date /
                 l_account_id / r_account_id / item / memo / entry_id 사용.
        date_window_days: 같은 절대 금액 bucket 안에서 두 거래가 같은
                          cluster 후보가 되는 날짜 차 상한. 기본 7. 가맹점
                          처리 지연, 정정 등 흔한 케이스 흡수.
        min_verdict: 결과 cluster 의 최소 verdict 강도. "possible" 이면
                     약한 신호 (금액+날짜만 일치, 계정 다름) 까지 포함.
                     "very_likely" 면 신중한 dedup 만.

    Returns:
        DupeCluster list — verdict 강한 순 → cluster 크기 큰 순 → 첫 entry_date
        오름차순 정렬. 비어있으면 빈 list.

    Notes:
        - money 가 None / 0 인 entry 는 신호 너무 약해 skip (가짜 cluster
          폭증 방지).
        - entry_id 가 같은 entry 는 중복 입력으로 간주, 한 번만 처리.
        - 본 함수는 pure — sqlite / 후잉 의존 없음. 테스트 용이.
    """
    min_rank = _VERDICT_ORDER[min_verdict]

    # entry_id 중복 제거 (안전망).
    seen_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for e in entries:
        eid = str(e.get("entry_id") or "")
        if eid and eid in seen_ids:
            continue
        if eid:
            seen_ids.add(eid)
        deduped.append(e)

    # 1) 절대값 금액 bucket. money None / 0 skip.
    buckets: dict[int, list[dict[str, Any]]] = {}
    for e in deduped:
        abs_money = _strip_money(e.get("money"))
        if abs_money is None or abs_money == 0:
            continue
        buckets.setdefault(abs_money, []).append(e)

    # 2) bucket 안 후보 쌍 → union-find.
    # parent: index in `deduped`. id_to_idx 로 lookup.
    id_to_idx: dict[int, int] = {id(e): i for i, e in enumerate(deduped)}
    parent: list[int] = list(range(len(deduped)))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    # pair_verdict cache: (idx_a, idx_b) → (Verdict, reasons). 같은 쌍이 여러
    # bucket 에 들어갈 일은 없지만 안전망 + cluster 강도 계산에도 재사용.
    pair_info: dict[tuple[int, int], tuple[Verdict, tuple[str, ...]]] = {}

    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        # 날짜 순으로 정렬, two-pointer 로 ±window.
        bucket_sorted = sorted(
            enumerate(bucket),
            key=lambda pair: _date(pair[1].get("entry_date")),
        )
        n = len(bucket_sorted)
        for i in range(n):
            _, ei = bucket_sorted[i]
            di = _date(ei.get("entry_date"))
            for j in range(i + 1, n):
                _, ej = bucket_sorted[j]
                dj = _date(ej.get("entry_date"))
                diff = _day_diff(di, dj)
                if diff is None or diff > date_window_days:
                    # 정렬돼 있으니 j 이후도 모두 window 밖 — break.
                    if diff is not None and diff > date_window_days:
                        break
                    # diff None (날짜 형식 이상) 은 같은 ei 의 다른 j 는 가능.
                    continue
                v, reasons = _pair_verdict(ei, ej)
                if _VERDICT_ORDER[v] < min_rank:
                    continue
                idx_a = id_to_idx[id(ei)]
                idx_b = id_to_idx[id(ej)]
                pair_info[(min(idx_a, idx_b), max(idx_a, idx_b))] = (v, reasons)
                _union(idx_a, idx_b)

    # 3) component 모으기.
    comp_map: dict[int, list[int]] = {}
    for idx in range(len(deduped)):
        root = _find(idx)
        comp_map.setdefault(root, []).append(idx)

    clusters: list[DupeCluster] = []
    for member_idxs in comp_map.values():
        if len(member_idxs) < 2:
            continue
        # cluster 안 가장 강한 pair_verdict 와 그 reasons.
        strongest: Verdict = "different"
        strongest_reasons: tuple[str, ...] = ()
        for i in range(len(member_idxs)):
            for j in range(i + 1, len(member_idxs)):
                a = min(member_idxs[i], member_idxs[j])
                b = max(member_idxs[i], member_idxs[j])
                info = pair_info.get((a, b))
                if info is None:
                    continue
                v, r = info
                if _VERDICT_ORDER[v] > _VERDICT_ORDER[strongest]:
                    strongest = v
                    strongest_reasons = r
        if _VERDICT_ORDER[strongest] < min_rank:
            continue

        members = [deduped[i] for i in member_idxs]
        # keep_suggestion — entry_date 가장 오래된 것 (먼저 기록), 동률이면
        # entry_id 사전순 작은 것.
        members_sorted = sorted(
            members,
            key=lambda e: (_date(e.get("entry_date")), str(e.get("entry_id") or "")),
        )
        keep_id = str(members_sorted[0].get("entry_id") or "") or None
        clusters.append(DupeCluster(
            entries=tuple(members_sorted),
            verdict=strongest,
            reasons=strongest_reasons,
            keep_suggestion=keep_id,
        ))

    # 최종 정렬: verdict 강한 순, 같으면 cluster 크기 큰 순, 같으면 첫
    # entry_date 오래된 순.
    clusters.sort(key=lambda c: (
        -_VERDICT_ORDER[c.verdict],
        -len(c.entries),
        _date(c.entries[0].get("entry_date")) if c.entries else "",
    ))
    return clusters


__all__ = [
    "DupeReport", "DupeCluster", "Verdict", "VERDICT_LABELS_KO",
    "evaluate_duplicates", "find_duplicate_clusters", "is_duplicate",
    "normalize_text", "merchant_similar",
]
