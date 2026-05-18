# 07. 필터 + 검색

## 목적

DataTable 컬럼 값을 기준으로 같은 값의 거래만 빠르게 추려본다. 인라인
태그도 같은 흐름. 윈도우 (최근 N 일) 밖의 매칭은 sqlite 캐시 / 백그라운드
fetch 로 점진 확장.

## 주요 경로

```
EntriesScreen
  ├─ ←/→ : _active_col 좌/우 이동 + 노란 cell marker.
  ├─ Enter (cell marker 활성 상태)
  │   └─ 현재 컬럼 값으로 filter_entries(_all_entries) → 결과 표시.
  │       └─ 윈도우 밖 매칭은 _filter_extra (background worker) 가 채움.
  └─ r 또는 c : action_clear_filter → 원본 복원.

item 컬럼 안 태그 단위 cursor
  └─ Enter → 같은 태그를 가진 거래로 filter.
```

## 단계별 흐름

### 컬럼 값 필터

1. 거래 row 에서 ←/→ → 노란 marker 가 어느 cell 인지 표시.
2. Enter → `filter_entries(_all_entries, column, target_entry)` 호출.
3. 같은 컬럼 값의 거래만 표시. Footer 에 "필터: 식비 (87건)" 같은 안내.
4. 다른 cell 로 가서 Enter → 필터 *교체* (누적 아님).
5. `r` 또는 `c` 로 해제.

### 점진 확장 (CL #52758+)

윈도우 밖 (`_all_entries` 미포함) 의 매칭은 즉시 보이지 않는다. 두 단계
로 채워진다:

1. **sqlite 캐시 즉시 조회** — `entries_cache` 테이블에 이전 호출 결과
   캐싱되어 있으면 instant.
2. **백그라운드 fetch worker** — 더 오래된 윈도우를 chunk 단위로 fetch
   해 `_filter_extra` 에 append + table 재렌더. 사용자가 필터를 바꾸면
   `_filter_epoch += 1` 로 진행 중 worker 가 자기 결과 폐기 (race
   방지).

### 태그 필터

1. item 컬럼 안에서 → / Tab 으로 *태그 단위 cursor*. 선택된 태그는 cyan.
2. Enter → 그 태그로 필터 — 컬럼 필터와 같은 흐름.

### 메모 substring 검색

1. `/` → 입력 prompt (또는 메뉴) → 메모 부분 문자열 매칭.
2. 같은 점진 확장 정책.

## 에지 케이스

- **빈 결과** — 윈도우 안 매칭 0 + 윈도우 밖에서 도착하기 전 → "필터
  결과 없음 (확장 중)" 안내. worker 가 결과를 가져오면 자동 채워짐.
- **매우 큰 결과 (수천 건)** — DataTable 의 성능 한계 + 후잉 cap (100
  건/페이지). 현재 정책: 보이는 만큼만, footer 에 "100건 + N건 더…"
  경고.
- **필터 도중 mutation** — 거래를 수정/삭제하면 `refresh_entries` 가
  필터를 잠시 해제. 사용자가 다시 켜야 함 (단순성 우선).

## 관련 코드

- [`filters.py`](../../tui/src/whooing_tui/filters.py) —
  `FILTERABLE_COLUMNS`, `filter_entries`.
- [`screens/entries.py:1450~1750`](../../tui/src/whooing_tui/screens/entries.py)
  — column marker / Enter dispatch / `_active_col` 관리.
- [`screens/entries.py: _filter_*`](../../tui/src/whooing_tui/screens/entries.py)
  — `_filter_extra`, `_filter_epoch`, 점진 확장 worker.
- [`core/entries_cache.py`](../../core/src/whooing_core/entries_cache.py)
  — schema v8 entries 캐시 레이어.
