# 05. 중복 거래 평가 + 정리

## 목적

같은 거래가 두 번 들어간 경우 (카드 명세서 + 수기 입력 겹침 / 좌우
계정 혼동 / 환불 처리 / 가맹점 처리 지연 등) 를 다층 휴리스틱으로
평가하고, 중복이면 keep 한 건 + 나머지 삭제.

## 주요 경로

```
EntriesScreen
  └─ space (2건 이상 선택) → m → "선택 N건 중복인지 평가…"
      └─ DuplicateEvalScreen (모달)
          ├─ on_mount → evaluate_duplicates(entries) → DupeReport
          ├─ verdict == "different" → ✅ 메시지 + 닫기 버튼만
          ├─ verdict ∈ (identical / very_likely / possible)
          │   ├─ Pair 별 평가 + DataTable (★ = 기본 keep)
          │   ├─ space (또는 클릭) → keep 변경
          │   └─ Enter | "선택만 남기고 삭제" 버튼 → dedup
          └─ ESC 닫기 (변경 없음)
```

## 평가 휴리스틱 (강한 매칭부터)

| Verdict | 의미 |
|---|---|
| **identical** | 모든 raw 필드 (money / date / 좌우 계정 / item / memo) byte-exact. |
| **very_likely** | 좌·우 계정 반대, item 공백·구두점만 차이, 금액 부호 반대 (환불/취소), 같은 날·같은 계정·같은 금액에 item 만 다름 (카드 import + 수기 겹침). |
| **possible** | 금액 동일 + 날짜 ±1, 또는 금액·날짜 일치하지만 계정 다름. 사람 판단 필요. |
| **different** | 위 어디에도 안 걸림. 별개 거래로 본다. |

`keep_suggestion` — 가장 오래된 entry_date, 동률이면 entry_id 사전순.

## 단계별 흐름

### 평가 시작

1. EntriesScreen 에서 `space` 로 2건 이상 선택 — Footer 의 selection
   카운트로 확인.
2. `m` (또는 한글 ㅡ) → 컨텍스트 메뉴에 "선택 N건 중복인지 평가…" 노출.
3. 선택 → `_evaluate_duplicates_worker` 가 `DuplicateEvalScreen` push.

### 중복이 아닌 경우

- verdict = `different` → ✅ "중복 아님" + 근거 + 닫기 버튼.
- 사용자 → 닫기 → 변경 없이 EntriesScreen 복귀.

### 중복일 경우

1. 좌측 verdict 라벨 (⚠️) + 근거 + pair-별 평가 텍스트.
2. DataTable 에 거래들 + ★ 마크된 keep 후보.
3. 사용자가 keep 후보 변경:
   - ↑/↓ 이동 → space (또는 k) → 그 row 만 keep.
   - 또는 row 클릭 → 같은 효과.
4. Enter 또는 "선택만 남기고 삭제" 버튼 → dedup 워커:
   - 각 entry 에 `delete_entry()` 호출 + 로컬 sqlite annotation 정리.
   - 실패 row 가 있으면 status 에 첫 사유 노출.
5. 성공 시 `dismiss(True)` → EntriesScreen 가 selection clear +
   `refresh_entries()`.

## 비고

- DuplicateEvalScreen 의 BINDINGS 는 space=keep priority — DataTable
  의 기본 space 동작 (cursor_type="row" 에선 거의 noop) 보다 먼저
  처리되어 keep 변경.
- pure function 평가 모듈은 sqlite / 후잉 의존이 없어 unit test 가 빠름
  ([`core/tests/test_dupes.py`](../../core/tests/test_dupes.py)).

## 관련 코드

- [`screens/dupe_eval.py`](../../tui/src/whooing_tui/screens/dupe_eval.py)
  — `DuplicateEvalScreen`.
- [`core/dupes.py`](../../core/src/whooing_core/dupes.py) —
  `evaluate_duplicates`, `DupeReport`, verdict 라벨.
- [`screens/entries.py:927~997`](../../tui/src/whooing_tui/screens/entries.py)
  — `action_evaluate_duplicates`, `_evaluate_duplicates_worker`,
  `_delete_many` callback.
