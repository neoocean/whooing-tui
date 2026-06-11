# 후잉가계부 기능 패리티 — 갭 분석 + 로드맵 (2026-06)

본 문서는 whooing-tui 가 **궁극적으로 후잉가계부(whooing.com) 와 동급의 기능**
을 갖춘다는 목표 아래, 후잉의 전체 기능 표면을 1회 전수 조사하고 TUI 의 현재
커버리지와 대조한 결과다. 각 갭에 대해 *가치 / 난이도 / 권장* 을 판정하고
우선순위 로드맵으로 정리한다. [`MAINTAINABILITY-REVIEW.md`](MAINTAINABILITY-REVIEW.md)
(유지보수성) · [`CODE-REVIEW-2026-06.md`](CODE-REVIEW-2026-06.md) (보안·성능) 와
나란히 **살아있는 백로그** 로 유지한다.

## 방법론

- **후잉 기능 표면의 출처:** 공식 Developer API 문서(`whooing://api-docs`,
  MCP 리소스) 의 `## 4. API Reference` 전 엔드포인트 + 공식 MCP 도구 표면.
  후잉의 "거의 모든 기능은 OpenAPI 로 직접 제어 가능"(문서 1장) 하므로 API
  엔드포인트 = 기능 표면으로 본다.
- **TUI 커버리지의 출처:** `client.py` 메서드, `screens/` 화면,
  `reports.py` 메뉴, F10 메뉴(`entries.py:_build_menus`) 를 직접 grep/read.
- 판정 기호: ✅ 완비 · ⚠️ 부분(메서드만 있고 UI 없음 등) · ❌ 없음 ·
  ➖ 의도적 비채택(TUI 설계상 다른 방식 채택 / 범위 밖).

---

## 1. 기능 매트릭스

| 후잉 기능 영역 | 후잉 API | TUI 현황 | 판정 |
|---|---|---|---|
| **거래 입력/수정/삭제** (entries CRUD + 일괄) | ✅ | `screens/entries.py` · `edit_entry.py` | ✅ |
| 최근거래 latest / latest_items(60일 suggest) | ✅ | `get_entries_latest` + 입력 자동완성 prefetch (0.84.0) | ✅ |
| 입력 자동완성 — items/latest_items | ✅ | 서버 최근 아이템 inline suggest (0.84.0) | ✅ |
| 계정 흐름 flow_of_account(_id) | ✅ | `screens/account_flow.py` (0.84.0) | ✅ |
| 일일 변동 changes_of_account_id/client/item | ✅ | `account_flow.py` (0.84.0) | ✅ |
| 외부데이터 입력 entries/outside (서버측 SMS 파싱) | ✅ | 없음 (자체 어댑터로 대체) | ➖ |
| **자주입력 거래** (frequent_items) | ✅ CRUD+sort | `screens/frequent_entries.py` (0.84.0) | ✅ |
| **매월입력 거래** (monthly_items) | ✅ | `screens/monthly_entries.py` | ✅ |
| **예산** (budget get/set/basic_total) | ✅ | `screens/budget_edit.py` (`set_budget`) | ✅ |
| **장기 예산목표** (budget_goal) | ✅ | `screens/goal_edit.py` (`set_budget_goal`) | ✅ |
| **월별 자본목표** (goal) | ✅ | `screens/goal_edit.py` (`set_goal`) | ✅ |
| **자산/부채/비용/수익 보고서** (report/summary) | ✅ | `screens/reports.py` | ✅ |
| 자금증감 (in_out) | ✅ | `reports.py` 메뉴 | ✅ |
| 캘린더 (calendar) | ✅ | `reports.py` 메뉴 | ✅ |
| 신용카드 청구 (bill) | ✅ | `reports.py` 메뉴 노출 (0.84.0) | ✅ |
| 체크카드 (checkcard) | ✅ | `reports.py` 메뉴 노출 (0.84.0) | ✅ |
| 사용자정의 보고서 행 (report_customs) | ✅ CRUD | `screens/report_customs.py` 생성·삭제 (0.84.0) | ✅ |
| **섹션 목록/선택** (sections list/default) | ✅ | `screens/sections.py` (읽기/선택) | ✅ |
| 섹션 CRUD/정렬 (create/update/delete/sort) | ✅ | `sections.py` CRUD+sort (0.84.0) | ✅ |
| **계정과목 CRUD** (accounts) + 정렬 | ✅ CRUD+sort | CRUD ✅ · 정렬은 `sort_accounts` 메서드만 (Tree UI 후속) | ⚠️ |
| **포스트잇** (post_it) | ✅ CRUD | 없음 | ❌ |
| 알림 (notifications) | ✅ | 없음 | ❌ |
| 사용자 프로필 (user get/update) | ✅ | 없음 | ❌ |
| 사용자/포인트 로그 (user_logs/point_logs) | ✅ | 없음 | ❌ |
| 클라우드 파일첨부 (upload) | ✅ | 로컬 sqlite 첨부로 대체 (시나리오 10) | ➖ |
| 쪽지 (messages, 유저 간 메신저) | ✅ | 없음 | ➖ |
| 게시판/커뮤니티 (bbs) | ✅ | 없음 | ➖ |

### 요약 수치

- ✅ 완비: 거래 CRUD · 매월입력 · **자주입력**(0.84.0) · 예산 · 장기/월별
  목표 · 핵심 보고서(report/in_out/calendar) · **카드 청구/체크카드**(0.84.0)
  · **사용자정의 보고서 행**(0.84.0) · **항목 흐름/변동**(0.84.0) · **입력
  자동완성**(0.84.0) · 섹션 선택+**CRUD/정렬**(0.84.0) · 계정 CRUD.
- ⚠️ 부분: accounts 정렬(메서드만, Tree UI 후속).
- ❌ 남은 신규 구현: 포스트잇 · 알림 · 사용자 프로필.
- ➖ 의도적 비채택: 클라우드 첨부(로컬 대체) · 쪽지 · 게시판 · outside 파싱.

### 구현 현황 — 0.84.0 (2026-06)

로드맵 항목 6종 구현·서브밋 완료 (각 CL 단위, `tui/CHANGELOG.md` 0.84.0):
**P1-A** 입력 자동완성 · **P1-B** 자주입력 · **P2-A** 카드 청구/체크카드 ·
**P2-B** 항목 흐름/변동 · **P2-C** 사용자정의 보고서 행 쓰기 · **P3-C** 섹션
CRUD/정렬. 남은 권장: 포스트잇(P3-A) · 알림(P3-B) · 계정 정렬 Tree UI.

---

## 2. TUI 가 이미 후잉 웹/공식앱을 넘어선 영역

패리티 목표가 "따라잡기" 만은 아니다 — TUI 는 이미 후잉에 없는 고유 가치를
다수 보유한다. 로드맵은 이 차별화를 **희석하지 않는 선에서** 채워야 한다.

- **로컬 sqlite 미러** — 후잉 API 가 모르는 메모/태그/첨부를 별도 보관
  (시나리오 10). sha256 dedup 첨부 + PDF/텍스트 미리보기.
- **해시태그 / 주석(annotation) + 일괄 태깅** (시나리오 06).
- **중복 거래 평가 + 일괄 스캔** — 휴리스틱(비-LLM), 2단계 모달 (시나리오 05).
- **반복 거래 누락 탐지** — 위의 대칭 기능, 0.83.0 신설 (시나리오 13).
- **수정 이력 / 소프트 삭제 / 휴지통 / 복원** (시나리오 11).
- **카드 명세서 import** — CSV/HTML/PDF 어댑터 + 영수증 메타 추출 (시나리오 04).
- **컬럼 필터 / 검색**, **한글 IME 자모 조합**, **P4 다중 기계 동기화** (시나리오 12).

> 후잉의 `entries/outside`(서버측 SMS 파싱) 를 비채택으로 둔 이유도 이것 —
> TUI 는 이미 자체 어댑터로 명세서를 클라이언트에서 파싱한다. outside 는
> *보완재*(어댑터가 못 읽는 포맷의 fallback) 로만 가치가 있다(§5 P4).

---

## 3. 우선순위 로드맵

체감 가치(후잉 일상 사용에서 얼마나 자주 닿는가) × 구현 난이도 순.

### P1 — 입력 경험의 핵심 (후잉의 본질)

후잉 웹 사용자가 가장 자주 닿는 것은 *빠른 거래 입력* 이다. 여기 두 항목이
패리티의 최대 격차.

**P1-A. 거래 입력 자동완성 / Suggest** — `❌`
- 후잉은 입력 시 항목·아이템·거래처를 서버 데이터로 자동완성한다:
  `entries/latest_items`(60일 중복제거), `entries/items_of_account_id`(항목별
  아이템), `entries/clients_of_account_id`(거래처), `account_ids_of_account`.
- TUI 는 현재 로컬 캐시만 참조 → 신규/타 기기 입력이 제안에 안 뜸.
- **처방:** `edit_entry.py` 의 item/계정 입력에 서버 suggest 소스 연결.
  `client.py` 에 `list_latest_items` / `items_of_account_id` /
  `clients_of_account_id` 추가 + 입력 위젯 자동완성. **난이도 중.**

**P1-B. 자주입력 거래 (Frequent Items)** — `❌`
- 매월입력(monthly_items)은 있으나 **자주입력(frequent_items) 이 없다.**
  후잉 웹의 1-탭 반복 입력(커피/점심/교통 등)에 대응하는 대칭 기능 부재.
- 공식 MCP 에 `frequent_items-list/create/update/delete/sort` 가 이미 존재 →
  `client.py` 메서드 + `screens/frequent_entries.py`(monthly_entries 패턴 복제).
- **처방:** 입력 메뉴에 "자주입력 거래 관리…" + 입력 다이얼로그에서 1키 채움.
  **난이도 하~중** (monthly_entries 가 청사진).

### P2 — 분석/조회 심화 + 빠른 보완

**P2-A. Bill / Checkcard 보고서 UI 노출** — `⚠️ quick win`
- `get_bill` / `get_checkcard` 메서드는 **이미 있는데** `reports.py` 좌측
  메뉴(`_MENU`, L191-198) 에 항목이 없어 사용자가 닿지 못함.
- **처방:** 메뉴 튜플 2줄 + 렌더러 추가. **난이도 하** (가장 싼 패리티 한 칸).

**P2-B. 계정 흐름 / 변동 조회** — `❌`
- `flow_of_account(_id)`(특정 계정 ↔ 전 계정 상대 증감), `changes_of_account_id`
  (항목 일일 변동), `changes_of_client`/`changes_of_item`, `items_of_account_id`
  (아이템별 금액) — 후잉의 "이 항목/거래처가 어디로 흘렀나" 분석.
- **처방:** 보고서 화면에 "항목 흐름" 뷰 — 항목 선택 → 상대 계정별/거래처별/
  아이템별 집계 표. **난이도 중.**

**P2-C. Report Customs 쓰기** — `⚠️`
- 현재 읽기전용(list/get). 후잉 API 는 `POST main/report_customs.json` 으로
  사용자 정의 보고서 행 생성/삭제 지원.
- **처방:** `create_report_custom` / `delete_report_custom` + 보고서 화면 편집.
  **난이도 중.**

### P3 — 보조 기능 (단일 사용자 가계부에 유효)

**P3-A. 포스트잇 (Post-it)** — `❌`
- 섹션·페이지별 메모(`post_it` CRUD). 가계부 운영 메모로 단일 사용자에게 유용.
  공식 MCP `post_it-list/create/update/delete` 존재.
- **처방:** 패널 또는 모달 화면. **난이도 중.**

**P3-B. 알림 (Notifications)** — `❌`
- `notifications.json` (최근 2주/50개). 문서 권장 폴링 주기 **≥5분**.
- **처방:** 시작 시 + 백그라운드 worker 로 미확인 알림 → status/배지.
  `@work` + 5분 throttle. **난이도 중** (폴링 수명주기 주의).

**P3-C. 섹션 CRUD / 정렬, 계정 정렬** — `❌`/`⚠️`
- 섹션은 현재 읽기/선택만. 계정은 CRUD 되나 정렬(`accounts/.../sort`) 없음.
- 변경 빈도 낮아 우선순위 낮지만 패리티 완성 항목. **난이도 하.**

### P4 — 낮은 가치 / 조회 전용 / 보완재

- **사용자 프로필 (user get/update) + user_logs/point_logs** — 주로 조회.
  TUI 일상 흐름에 거의 안 닿음. 낮은 가치.
- **entries/outside 서버측 파싱 위임** — TUI 자체 어댑터가 못 읽는 SMS/문자
  포맷의 *fallback* 으로만. 자체 어댑터와 중복이라 보완재.

---

## 4. 비채택 권고 (➖)

목표는 "후잉과 같은 수준의 **가계부** 기능" 이지 후잉 웹의 모든 픽셀 복제가
아니다. 다음은 단일 사용자 power-tool TUI 의 범위 밖으로 보고 **명시적 비채택**
을 권한다(근거 포함). 향후 사용자 요구가 생기면 재검토.

- **게시판/커뮤니티 (bbs)** — 다중 사용자 소셜 기능. 댓글/대댓글/추천 등
  본질적으로 브라우저 경험. TUI 로 옮길 가치 낮음.
- **쪽지 (messages)** — 유저 간 메신저. 가계부 본연과 무관. 비채택.
- **클라우드 파일첨부 (upload)** — 시나리오 10 에서 **의도적으로 로컬 sqlite
  첨부** 를 택함(오프라인·프라이버시·P4 동기화). 단, 향후 "선택적 클라우드
  미러" 옵션은 열어둘 여지 — 지금은 비채택.

---

## 5. 적용 순서 제안 (체감 효과순)

1. **P2-A** Bill/Checkcard 메뉴 노출 — 메서드 재사용, 2줄. 가장 싼 한 칸.
2. **P1-B** 자주입력 거래 — monthly_entries 패턴 복제, MCP 도구 기존재.
3. **P1-A** 입력 자동완성 — 후잉 입력 UX 의 핵심 격차.
4. **P2-B** 계정 흐름/변동 분석 뷰.
5. **P3-A / P3-B** 포스트잇 · 알림.
6. **P2-C / P3-C** report_customs 쓰기 · 섹션 CRUD · 계정 정렬.

> 각 항목 착수 시: 신규 화면은 `screens/` 패턴 + `tui/tests/` 1:1 통합
> 테스트 한 쌍(`make test`), client 메서드는 `WhooingClient` +
> `CachedWhooingClient` 양쪽 동시 추가(이중 표면 — CODE-REVIEW §1-C 참조).
