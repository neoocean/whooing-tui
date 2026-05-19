# 05. 중복 거래 평가 + 정리

## 두 가지 진입점

본 TUI 에는 중복 거래 처리 워크플로가 **두 가지** 있다 — 사용자 의도에 따라
별도 화면.

| 진입 방식 | 화면 | 목적 |
|---|---|---|
| **선택 중심** — space 로 2~N건 골라 m → "선택 N건 중복인지 평가" | `DuplicateEvalScreen` (CL #52815+) | 이미 의심 가는 거래 한 묶음에 대한 *수동* 평가 + 즉시 dedup |
| **메뉴 중심** — 입력 메뉴 → "중복 거래 검사… (지난 3년)" | `DupeScanOverviewScreen` → `DuplicateScanScreen` (CL #52963+, 0.77.0+ 영구화 추가) | 전체 ledger 에서 cluster 들을 *자동* 탐색 + 사용자 안내된 1개씩 정리 |

둘 다 같은 `core/dupes.py` 평가 휴리스틱을 공유 — 차이는 데이터 입력
방식 (선택된 N건 vs 3년치 자동 발견) 과 UI 흐름이다.

## 진입점 A — 선택 중심 평가 (DuplicateEvalScreen)

### 목적

같은 거래가 두 번 들어간 경우 (카드 명세서 + 수기 입력 겹침 / 좌우
계정 혼동 / 환불 처리 / 가맹점 처리 지연 등) 를 다층 휴리스틱으로
평가하고, 중복이면 keep 한 건 + 나머지 삭제.

### 흐름

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

### 평가 휴리스틱 (강한 매칭부터)

| Verdict | 의미 |
|---|---|
| **identical** | 모든 raw 필드 (money / date / 좌우 계정 / item / memo) byte-exact. |
| **very_likely** | 좌·우 계정 반대, item 공백·구두점만 차이, 금액 부호 반대 (환불/취소), 같은 날·같은 계정·같은 금액에 item 만 다름 (카드 import + 수기 겹침). |
| **possible** | 금액 동일 + 날짜 ±1, 또는 금액·날짜 일치하지만 계정 다름. 사람 판단 필요. |
| **different** | 위 어디에도 안 걸림. 별개 거래로 본다. |

`keep_suggestion` — 가장 오래된 entry_date, 동률이면 entry_id 사전순.

### 단계별 흐름

1. EntriesScreen 에서 `space` 로 2건 이상 선택 — Footer 의 selection 카운트로 확인.
2. `m` (또는 한글 ㅡ) → 컨텍스트 메뉴에 "선택 N건 중복인지 평가…" 노출.
3. 선택 → `_evaluate_duplicates_worker` 가 `DuplicateEvalScreen` push.
4. verdict = `different` 면 ✅ "중복 아님" + 닫기 버튼만.
5. 그 외:
   - 좌측 verdict 라벨 (⚠️) + 근거 + pair-별 평가 텍스트.
   - DataTable 에 거래들 + ★ 마크된 keep 후보. ↑/↓ + space (또는 k / 클릭) 로 keep 변경.
   - Enter 또는 "선택만 남기고 삭제" 버튼 → dedup 워커 (delete_entry + 로컬 annotation 정리).
6. 성공 시 `dismiss(True)` → EntriesScreen 가 selection clear + `refresh_entries()`.

### 비고

- DuplicateEvalScreen 의 BINDINGS 는 space=keep priority — DataTable 의 기본 space 동작보다 먼저 처리.
- pure function 평가 모듈은 sqlite / 후잉 의존이 없어 unit test 가 빠름
  ([`core/tests/test_dupes.py`](../../core/tests/test_dupes.py)).

### DuplicateEvalScreen 관련 코드

- [`screens/dupe_eval.py`](../../tui/src/whooing_tui/screens/dupe_eval.py)
  — `DuplicateEvalScreen`.
- [`core/dupes.py`](../../core/src/whooing_core/dupes.py) —
  `evaluate_duplicates`, `DupeReport`, verdict 라벨.
- [`screens/entries.py`](../../tui/src/whooing_tui/screens/entries.py)
  의 `action_evaluate_duplicates` / `_evaluate_duplicates_worker` /
  `_delete_many` callback.

---

## 진입점 B — 메뉴 중심 일괄 스캔 (DupeScanOverviewScreen + DuplicateScanScreen)

CL #52963 (0.76.0) 도입, CL #52989 (0.77.0) 에서 sqlite 영구화 + 2단계
UI 로 확장.

### 목적

사용자가 *어떤 거래가 중복인지 모르는* 상황에서 ledger 전체를 자동
탐색 → cluster 들을 한 화면에서 분포 확인 → 묶음 단위로 정리.

### 흐름

```
EntriesScreen
  └─ 입력 메뉴 → "중복 거래 검사… (지난 3년)"
      └─ DupeScanRangeModal (CL #53006+ — 범위 선택)
          │   1개월 / 3개월 / 6개월 / 1년 / 3년 (기본) / 5년
          │   default 는 이전 선택 (세션 단위 기억) 또는 3년.
          │   Esc → wizard 취소.
          ▼
      └─ ScanProgressModal (캐시 hit 면 잠깐만 표시)
          │   "📊 거래 fetch 중… {start} ~ {end} ({범위명})"
          │   "🔍 중복 cluster 검색 중… N 건 분석"
          │   "💾 결과 저장 중… M 개 cluster sqlite 영구화"
          ▼
      └─ DupeScanOverviewScreen (Stage 1 — 전체 분포)
          │   범위 · 캐시/네트워크 출처 · 전체 N · 정리됨 X · 남음 Y
          │   DataTable: [#, 상태, 강도, 날짜, 금액, 건수, 적요]
          │
          ├─ Enter / R     → DuplicateScanScreen (해당 cluster 부터 정리)
          ├─ F5 / Ctrl+R   → repo.clear → fetch 재요청 → overview 갱신
          └─ Esc           → entries 복귀 (정리된 게 있으면 재로드)

      └─ DuplicateScanScreen (Stage 2 — cluster 1개씩)
          │   "N / T  ·  verdict" + 근거 + DataTable
          │   각 row: ✓ 삭제 / ✗ 보존 (Space toggle)
          │
          ├─ Enter         → 삭제 성공 → repo.update_status('resolved')
          │                  → 다음 unresolved cluster 자동 이동
          ├─ n / →          → skip (DB 상태 변경 X — 다음 스캔에서 다시 노출)
          ├─ p / ←          → 이전 cluster (이미 정리된 것도 readonly 표시)
          └─ Esc            → overview 복귀 (자동 갱신)
```

### 알고리즘 — `find_duplicate_clusters` (record-linkage)

표준 blocking + windowing + scoring + union-find:

1. **blocking** — `|money|` 절대값 bucket. 좌우 반전·환불 (부호 반대) 도
   같은 bucket 에 모음. money=0/None 은 신호 약해 skip.
2. **windowing** — bucket 안 entry_date 정렬 + two-pointer 로 ±7일 안 쌍.
3. **scoring** — 기존 `_pair_verdict` 재사용 (identical / very_likely /
   possible / different).
4. **clustering** — union-find 로 transitive 연결 (A↔B + B↔C → 한 cluster).
5. **정렬** — verdict 강한 순 → cluster 크기 큰 순 → 첫 entry_date 오래된 순.

복잡도 — 평균 윈도우 거래 k 개 (k « N) 면 O(N·k). 실 운영 ledger
(~5000건) 도 1~2초.

### sqlite 영구화 정책 (CL #52989 0.77.0+)

신규 테이블 `dupe_scan_clusters` (schema v9) — 한 row = 한 cluster.

```sql
section_id, scan_range_start, scan_range_end, scanned_at,
verdict, reasons_json, keep_suggestion,
entries_json,                -- JSON list[dict], 확장 필드 보존
status,                      -- pending | resolved | skipped
resolved_at
```

**워커 진입 시 cache 결정**:
- `repo.has_open_scan(section, range)` → 같은 범위의 `pending` 이 하나라도
  있으면 **fetch skip** — sqlite 에서 그대로 로드.
- 없으면 종래 fetch + cluster 분석 → `repo.save_scan` → overview.
- F5 → `repo.clear_scan` + fetch 재요청 (사용자 명시적 갱신).

**자동 상태 전이**:
- Enter 후 deletion 성공 → 자동 `status='resolved'`. 다음 스캔에서
  재등장 안 함.
- skip (n) → status 그대로 `pending` (다음 스캔에서 다시 노출).
- 같은 범위의 모든 cluster 가 resolved 되면 → 다음 스캔은 자동 재fetch
  (새 중복 발견 가능성).

### ScanProgressModal — 진행 안내 popup (CL #52977+)

fetch / 분석 단계 동안 본 화면 위에 작은 popup. BINDINGS 없음 (작업
완료 전 닫히지 않음). `set_activity(text)` 로 단계별 본문 갱신.

```
        ╭─ 🔍 중복 거래 검사 중 ─────╮
        │    📊 거래 fetch 중…         │
        │  20230520 ~ 20260519 (3년치) │
        │    잠시만 기다려주세요…       │
        ╰──────────────────────────────╯
```

### 입력 메뉴 wiring

`screens/entries.py · _build_menus` 의 "입력" MenuSpec 에 `MenuItem`
하나 추가:
```python
MenuItem("중복 거래 검사… (지난 3년)", "scan_duplicates")
```

`action_scan_duplicates` (sync) — `_scan_duplicates_worker` (@work) 호출.
worker 가 메뉴 dispatch 즉시 status + log.info 로 피드백
(CL #52968+ "아무 일도 안 일어남" 회귀 방지).

### 메뉴 중심 스캔 관련 코드

- [`screens/dupe_scan_overview.py`](../../tui/src/whooing_tui/screens/dupe_scan_overview.py)
  — `DupeScanOverviewScreen` (Stage 1 결과 목록).
- [`screens/duplicate_scan.py`](../../tui/src/whooing_tui/screens/duplicate_scan.py)
  — `DuplicateScanScreen` (Stage 2 cluster 1개씩) + `ScanProgressModal`
  (진행 안내) + `DupeScanRangeModal` (CL #53006+ 범위 선택).
- [`dupe_scan_repo.py`](../../tui/src/whooing_tui/dupe_scan_repo.py)
  — `DupeScanRepository` + `StoredCluster` dataclass.
- [`core/dupes.py`](../../core/src/whooing_core/dupes.py)
  의 `find_duplicate_clusters` (bulk 스캐너) + `DupeCluster`.
- [`screens/entries.py`](../../tui/src/whooing_tui/screens/entries.py)
  의 `action_scan_duplicates` / `_scan_duplicates_worker` /
  `_fetch_and_save_dupe_clusters` helper.
