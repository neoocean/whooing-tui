# 시나리오 13 — 반복 거래 누락 탐지

## 목적

정기 구독료·월세·급여처럼 **규칙적으로 반복되는** 거래가 어느 회차에 빠졌는지
휴리스틱(비-LLM)으로 찾아 보여준다. 중복 탐지(시나리오 05)가 *과잉 입력* 을
잡는다면, 본 기능은 그 반대 — *있어야 하는데 빠진* 거래를 잡는다.

이전에는 이런 누락을 LLM(대화/MCP)으로 일일이 살폈으나, 이제 앱에 내장된
순수 휴리스틱으로 같은 일을 결정적·반복가능하게 수행한다.

## 주요 경로

`입력` 메뉴 → `반복 거래 누락 검사…` (action `scan_recurring`)
→ 범위 선택 popup → (fetch + 분석) → 누락 시리즈 결과 화면.

## 단계별 흐름

1. **범위 선택** (`RecurringScanRangeModal`) — 6개월 / 1년(기본) / 2년 / 3년 /
   5년. 주기 추정에는 충분한 과거가 필요해 기본 1년(월간 12회 확보).
2. **캐시 확인** — 같은 (섹션, 범위)에 아직 처리 안 한(pending) 결과가
   sqlite 에 있으면 후잉을 다시 부르지 않고 즉시 로드(중복 검사와 같은 정책).
3. **fetch + 분석** (`RecurringScanProgressModal`) — 거래를 받아
   `detect_recurring_omissions(entries, as_of=오늘)` 실행. 진행 단계를 popup 에
   표시.
4. **결과 화면** (`RecurringOmissionScreen`):
   - 표: 주기(매주/격주/매월/분기/매년) · 항목 · 누락(연체/사이누락) · 마지막
     회차 · 대표 금액.
   - `↑/↓` 로 시리즈 이동 시 하단 상세에 빠진 회차 날짜가 펼쳐진다.
   - `h` — 이 시리즈 **처리함**(누락분을 입력했거나 직접 처리). 다시 안 보임.
   - `d` — **무시**(실제 반복이 아니거나 의도적으로 중단). 다시 안 보임.
   - `F5` — 후잉에서 새로고침. `Esc` — 닫기.

> 처리/무시는 즉시 sqlite 에 기록되므로, 같은 범위를 재검사해도 이미 처리한
> 시리즈는 다시 나타나지 않는다.

## 휴리스틱 요약

- **그룹핑**: (왼쪽계정, 오른쪽계정, 정규화 item) 으로 시리즈를 묶는다.
  금액은 공과금처럼 변동할 수 있어 key 에서 제외하고 중앙값을 대표로 표시.
- **주기 분류**: 근접(±3일) 거래는 한 회차로 병합 → 연속 간격의 **중앙값** 으로
  매주(7)/격주(14)/매월(~30)/분기(~91)/매년(~365) 분류.
- **규칙성 게이트**: 최소 3회차 + 간격이 주기 정수배 ± 허용오차에 드는 비율이
  0.6 이상이어야 시리즈로 인정 → 우연히 비슷한 거래 무리 배제.
- **누락 투영**: 첫 회차에서 주기마다 기대 날짜를 투영(달 기반은 달력 연산으로
  28~31일 변동·요일 이동 흡수)하고, 기대 ± 허용오차 안에 실제 거래가 없으면
  누락.
  - `gap` — 실제 거래 *사이* 의 누락(재개됐으므로 진짜 깜빡 누락, 강한 신호).
  - `overdue` — 마지막 회차 *이후* 연체. 허용오차+유예가 지나야 보고.
- **오탐 차단**: 연체가 3주기를 초과하면 '구독 종료'로 보고 연체 보고 안 함
  (해지한 구독을 매달 누락이라 알리지 않음).

## 관련 코드

- `core/src/whooing_core/recurring.py` — 순수 휴리스틱. `find_recurring_series`,
  `detect_recurring_omissions`, `RecurringSeries`/`MissingOccurrence`,
  `CADENCE_LABELS_KO`.
- `tui/src/whooing_tui/recurring_scan_repo.py` — `RecurringScanRepository`
  (sqlite `recurring_scan_series` 테이블, status pending/handled/dismissed).
- `tui/src/whooing_tui/screens/recurring_scan.py` — `RecurringScanRangeModal`,
  `RecurringScanProgressModal`, `RecurringOmissionScreen`.
- `tui/src/whooing_tui/screens/entries.py` — `action_scan_recurring`,
  `_scan_recurring_worker`, `_fetch_and_save_recurring`, "입력" 메뉴 항목.
- `core/src/whooing_core/db.py` — schema v11, `recurring_scan_series` 테이블.

## 테스트

- `core/tests/test_recurring.py` — 휴리스틱 단위 (13건).
- `tui/tests/test_recurring_scan_repo.py` — 영구화 round-trip (8건).
- `tui/tests/test_recurring_scan.py` — 메뉴 wiring + 통합 흐름 (4건).
