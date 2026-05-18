# 유지보수성 + LLM 친화도 검토

본 문서는 코드 전체를 1회 감사한 결과 + 구체적 개선 제안. **실행하지
않은 백로그** — 어느 항목이 가치 있는지는 사용자 / 담당자 합의 후 별
CL 로 배치. 우선순위는 *영향 / 비용* 비. 본 문서는 의사 결정 자료이며
시나리오 문서 ([scenarios/](scenarios/)) 와 달리 사용자 가시 기능을
설명하지 않는다.

## 요약

| 영역 | 가장 큰 문제 | 한 줄 처방 |
|---|---|---|
| God object | `screens/entries.py` 가 ~3000 줄 | UseCase / Repository 패턴으로 추출 |
| 타입 안정성 | `client.py` 의 `Any` 75+ | TypedDict + models.py 일원화 |
| 반복 패턴 | `try/except → set_status(error=True)` 가 화면마다 | `@safe_action` 데코레이터 |
| LLM 네비게이션 | 루트에 `CLAUDE.md` 없음 | 진입점 / 모듈 맵 / 핵심 클래스 인덱스 |
| 매직 상수 | `_SERVER_PAGE_CAP=100`, `0xD7A3` 등 흩어짐 | `constants.py` 일원화 |

## 카테고리별 항목

### 1. 모듈 크기 / God object (높은 우선순위)

- **`screens/entries.py` (~3000 줄)** — EntriesScreen 한 클래스에
  필터링·페이지네이션·네비게이션·삭제·메뉴 dispatch·태그 인라인 렌더
  까지 모두. 130+ try/except.
  **처방**: `EntryRepository(client, db)` 추출 (CRUD + 캐시 동기화),
  `EntryFilterCombiner` (점진 확장), `PaginationState` 데이터 클래스.
  실 effort: 2~3 CL. 영향: 신규 기능 / 회귀 수정 비용 절반.

- **`client.py` (~1100 줄, 80+ 메서드)** — HTTP 전송 + 재시도 + 응답
  강제 변환 + endpoint wrapper 가 한 클래스.
  **처방**: `WhooingTransport` (HTTP+retry), `WhooingAPI` (라우팅),
  endpoint 그룹별 (`EntriesAPI`, `AccountsAPI`, `ReportsAPI`) 분리.
  영향: mypy/IDE 추론 가능 + 신규 endpoint 추가 비용 1/3.

- **`screens/reports.py` 의 160+ 줄 메서드** — UI 빌드 + 데이터 집계
  + 캐싱이 섞임.
  **처방**: `ReportBuilder(client)` helper 로 데이터 / 렌더 분리.

### 2. 네이밍 / 발견성

- **`screens/entries_compact.py`** — 화면 모듈에 utility 함수 (`is_hangul`,
  `abbreviate_account_name`) 가 숨음. 다른 화면이 import 하면 순환 위험.
  **처방**: `whooing_tui/text_utils.py` 로 추출.
- **`data.py`** — 모듈 docstring 없음. 함수 4개의 책임 (path / init /
  open_rw 등) 이 분산.
  **처방**: 모듈 상단에 "Data management API: data_dir() → root,
  db_path() → SQL, init_shared_schema() → bootstrap" 주석.
- **`models.py`** — 후잉 도메인과 로컬 도메인 (TagRecord 등) 이 한
  파일에 혼재.
  **처방**: 모듈 docstring 에 "REST response schemas (Entry/Account) +
  local-only (TagRecord/AnnotationMeta)" 인덱스 추가.

### 3. 모듈 경계

- **EntriesScreen ↔ data.py 직접 결합** — `tui_data.open_rw()` 를 화면
  안에서 호출, `_persist_local`/`_purge_local` 도 화면 책임.
  **처방**: `EntryRepository` 어댑터로 캡슐화. 화면은 "거래를 저장해" /
  "삭제해" 만 호출.
- **`screens/edit_entry.py`** — `EntryDraft` (데이터) + `ConfirmModal`
  (유틸) + `EntryEditDialog` (UI) 가 한 파일.
  **처방**: `EntryDraft` 를 `models.py` 로 이동, `ConfirmModal` 을
  `widgets/` 으로.

### 4. 반복 패턴 (높은 ROI)

- **모든 `action_*` 의 try/except → set_status** 가 40+ 회 반복.
  **처방**: `@safe_action` 데코레이터.
  ```python
  def safe_action(fn):
      async def _wrap(self, *a, **k):
          try:
              return await fn(self, *a, **k)
          except ToolError as e:
              self.set_status(f"{fn.__name__} 실패 [{e.kind}] {e.message}", error=True)
          except Exception as e:
              log.exception("%s failed", fn.__name__)
              self.set_status(f"{fn.__name__} 실패 (INTERNAL): {e}", error=True)
      return _wrap
  ```
- **mutation worker 의 동일 try/except** — `_submit_create`,
  `_submit_update`, `_submit_delete` 가 같은 모양.
  **처방**: `AsyncWorkerMixin` 또는 `@work_with_status` 데코레이터.

### 5. 타입 안정성

- **`client.py` 의 `Any` 반환 75+** — IDE / mypy / LLM 모두 추론 불가.
  **처방**: 점진적으로 `TypedDict`:
  ```python
  class EntryResponse(TypedDict):
      entry_id: str
      entry_date: str
      money: int
      l_account_id: str
      r_account_id: str
      item: str
      memo: NotRequired[str]
  ```
  endpoint 별 하나씩 도입. 효과: 새 endpoint 추가 시 missing field 가
  컴파일 타임에 잡힘.

### 6. 매직 상수 / 흩어진 정책

- `_SERVER_PAGE_CAP=100`, `365*5`, `±7`, `0xD7A3`, `0xAC00` 등이
  여기저기.
  **처방**: `whooing_tui/constants.py`:
  ```python
  WHOOING_SERVER_PAGE_CAP = 100
  WINDOW_STEP_DAYS = 7
  MAX_WINDOW_DAYS = 365 * 5
  HANGUL_SYLLABLE_FIRST = 0xAC00
  HANGUL_SYLLABLE_LAST = 0xD7A3
  ```

### 7. LLM 친화도

- **루트 `CLAUDE.md` 부재** — 본 monorepo 의 진입점 / 모듈 맵을 LLM
  이 즉시 알 수 있는 문서가 없음.
  **처방**: `/CLAUDE.md`:
  ```
  # Whooing TUI — for AI assistants
  ## Entry points
  - whooing.py — sys.path bootstrap → cli.main()
  - tui/src/whooing_tui/cli.py: main() / run_app()
  - tui/src/whooing_tui/app.py: WhooingTuiApp
  ## Module map
  - core/ — DB / adapters / preview / dupes (pure, no Textual deps)
  - tui/ — Textual UI, depends on core
  ## Key classes
  - EntriesScreen (screens/entries.py) — initial screen, 가장 큰 화면
  - WhooingClient (client.py) — REST + cache wrapper
  ## Scenarios
  - docs/scenarios/ — 사용자 워크플로 단위 가이드.
  ```

- **상태 머신 암묵적** — 화면 전이 (sections → accounts → entries →
  edit) 가 action 메서드 안에 흩어짐.
  **처방**: `ScreenFlow` enum + 다이어그램 (DESIGN.md 의 mermaid 로).

- **각 모듈 상단 docstring 누락** — `filters.py`, `ime.py`, `cache.py`
  가 한 줄 모듈 설명 없음.
  **처방**: 한 줄 추가 — "IME utilities for Hangul composition",
  "Caching layer for client responses".

### 8. 문서 / 코드 드리프트

- **README.md** — wrapper archived 2026-05-10 명시했지만 `official_mcp.py`
  이 여전히 보고서 위임 용으로 사용됨 — 독자가 혼동 가능.
  **처방**: "MCP wrapper 는 archived 이나 보고서 위임용 `official_mcp.py`
  는 별도 컴포넌트" 한 줄 추가.
- **`config.entries.default_window_days`** — 코드 어디서 정의되는지
  불명. constant 로 노출 안 됨.
  **처방**: `config.py` 에 `DEFAULT_WINDOW_DAYS = 30` 명시.

## 권장 적용 순서

1. **`CLAUDE.md` + 모듈별 한 줄 docstring** (1 CL, 30분) — 가장 비싼
   비용 대비 LLM 협업 효율 큰 향상.
2. **`@safe_action` 데코레이터** (1 CL, 2시간) — try/except 보일러
   ~40 곳 제거.
3. **`constants.py` 일원화** (1 CL, 1시간) — 매직 상수 모음.
4. **`EntryRepository` 추출** (2~3 CL, 1~2일) — entries.py 의 sqlite
   직접 호출 제거. 신규 기능 비용 대폭 감소.
5. **`client.py` 분리** (3~5 CL, 2~3일) — 가장 큰 effort. 새 endpoint
   가 잦아지면 가치 크지만 안정적인 동안은 후순위.

각 단계는 *충분히 작은* CL — 하나씩 review + 회귀 검증.

## 비-적용 추천

- **테스트 추가만을 위한 추가 추상화** — `Protocol` / `Mock` 도입은
  *현재* 테스트가 fake client 패턴으로 충분히 동작 (~977 통과).
- **Textual 의 worker / 이벤트 model 우회** — 현재 `@work(exclusive=True)`
  + `group=` 정책이 race / cancel 처리에 충분. 추가 추상화는 비용 ↑.

## 관련 자료

- 이 검토의 기반 데이터: 본 CL 의 Explore agent 감사 (요약본).
- 본 검토 결과를 적용한 후에는 본 문서 자체도 *적용 항목 stamp* + 새
  결과만 남기는 식으로 정리 — 이 문서는 *살아있는 백로그*.
