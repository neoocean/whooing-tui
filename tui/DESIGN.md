# whooing-tui — 설계 노트

> 이 문서는 **현재 구조** 와 **다음 단계의 의도** 를 적어둔다. 코드와 문서가
> 어긋나면 코드가 진실이고, 이 문서는 그 진실의 *왜* 를 보존한다.

> **다이어그램 가이드라인**: 박스/화살표 구조의 다이어그램은 모두
> [mermaid](https://mermaid.js.org/) 로 작성한다 (ASCII art 금지). 단,
> 단순한 listing (디렉토리 트리, 파일 목록) 은 markdown 표로 — 머메이드
> 변환이 가독성을 떨어트리는 경우.

## 1. 목적과 범위

후잉 가계부를 터미널에서 다룬다. 같은 워크스페이스의
`whooing-mcp-server-wrapper` 가 **LLM 호스트(Claude Desktop / Code)** 를
대상으로 하는 반면, 본 도구는 **사람이 직접 키보드로** 가계부를 다룰 때를
위한 것이다. 두 도구는 서로 직교한다 — 같은 후잉 REST API 를 같은 인증
규칙으로 두드린다.

## 2. 다른 도구와의 관계

```mermaid
flowchart TB
    API[("같은 후잉 REST API<br/>+ 동일 토큰 규칙")]
    API --> TUI["<b>whooing-tui</b><br/>(사람·터미널)"]
    API -.archived 2026-05-10.-> WRAPPER["<b>whooing-mcp-server-wrapper</b><br/>monorepo의 mcp/ 에 보존<br/>(LLM·MCP, archived)"]
    API --> OFFICIAL["<b>whooing.com 공식 MCP</b><br/>(LLM·MCP, 외부)"]
    TUI -. mcp_bridge.py (deprecated) .-> WRAPPER
```

본래 핵심 라이브러리(REST 클라이언트·인증·날짜·에러 매핑) 는 **TUI 와
wrapper 가 의도적으로 코드 중복** 으로 공유하기 위해 만들어졌다. 한 패키지로
묶지 않은 이유 (당시):

- wrapper 는 `mcp>=1.0` / `playwright` / `pdfplumber` 등 무거운 의존성을
  싣는다. TUI 사용자는 이걸 받을 이유가 없다.
- wrapper 의 `tools/`, `parsers/` 는 LLM 도구 정의에 묶여 TUI 와는
  결합이 다르다.
- 한 쪽 변경이 다른 쪽을 깨지 않도록 분리.

**wrapper 종료 (archived 2026-05-10) 후 현재**: 라이브러리 중복은 그대로
유지 — 표면이 작고 안정 (auth/dates/errors 각 100줄 안팎) 해서 추출 비용
이득이 적고, 미래 새 도구 합류 가능성에 대한 옵션 가치 보존.

## 3. 아키텍처

### 3.0 모듈 인벤토리 (0.60.0)

| 모듈 | 책무 |
| --- | --- |
| `__init__.py` | 버전 (`__version__` — pyproject 와 동기, CL #52740+ 강제) |
| `__main__.py` | 엔트리: `python -m whooing_tui` |
| `cli.py` | argparse + 헤드리스 서브커맨드 dispatch (`sections` / `accounts` / `entries` / `gc-attachments` / `export-attachments`) |
| `auth.py` | `WhooingAuth` — 토큰 헤더 + 마스크 |
| `client.py` | httpx 기반 후잉 REST + `CachedWhooingClient` + `call_official_tool(name, args)` helper (CL #52755+, 공식 MCP 위임용) |
| `config.py` | TOML config 로더 |
| `dates.py` | KST YYYYMMDD/YYYYMM 유틸 + 1년 분할 |
| `errors.py` | HTTP → `ToolError` 매핑 + secret 마스크 |
| `models.py` | Pydantic `Section` / `Account` / `Entry` / `ToolError` |
| `state.py` | `SessionState` (활성 섹션 + 계정 캐시 + 양방향 인덱스) |
| `cache.py` | `CacheStore` — sqlite 기반 accounts/entries 캐시 (Phase 3, 메모리 TTL) |
| `data.py` | 로컬 sqlite + 첨부 storage 위치 (`<project>/attachment/`) + 마이그레이션 + P4 sync hook |
| `filters.py` | column-별 필터 술어 (date/left/right/item) + **memo substring** (CL #52756+, `memo_keywords()` 2글자 이상 토큰) |
| `ime.py` | 한글 ↔ 영문 두벌식 + `bind_ko` + 초성 분해 (`choseong_of` / `to_choseong_string`) |
| `p4_sync.py` | P4 자동 동기화 — `_do_submit_multi` 는 **numbered CL 패턴** (CL #52748+, `submit -d <abs path>` syntax 오류 fix) — `_create_numbered_change` + `reconcile -c <CL>` + `submit -c <CL>` |
| **`official_mcp.py`** | **신규 (CL #52755+)** 후잉 공식 MCP server (`https://whooing.com/mcp`) 의 minimal JSON-RPC client (SSE 응답도 지원) |
| `app.py` | Textual App + `_ShutdownModal` (CL #52761+, q 종료 시 진행 모달 + thread executor 로 flush) + `on_unmount`(P4 flush 안전망) |
| `widgets/menubar.py` | F10/Alt/Alt+M/Alt+F 풀다운 메뉴바 (MenuBar/MenuPopup/MenuBarMixin) + 마우스 클릭 진입 (CL #52759+, `MenuBar.MenuClicked` message + `menubar_index_at_offset`) |
| `widgets/input_modal.py` | 통합 입력 modal (CL #51156+, InputModal/TextAreaModal) |
| `widgets/confirm.py` | 통합 yes/no modal — y/n + ㅛ/ㅜ IME 매칭 (CL #52724+) |
| `screens/__init__.py` | Screen 패키지 |
| `screens/entries.py` | EntriesScreen — 거래내역 표 + 메뉴바 + 컴팩트 + 첨부 indicator (📎N) + multi-select (▣) + **context menu (m 키, CL #52763+)** + **점진적 캐시 필터 확장** (CL #52758+, `_apply_filter` + `_expand_filter_in_past` worker + `_filter_epoch`) |
| `screens/entries_compact.py` | pure helpers: 약어 / 임계값 / 컬럼 visibility |
| `screens/sections.py` | SectionPickerScreen (`s`) |
| `screens/accounts.py` | AccountsScreen (`a`) + AccountEditDialog + 메뉴바 통합 |
| `screens/edit_entry.py` | EntryEditDialog + 위젯 + tags inline 힌트 (이미 입력된 태그 hint 제외, CL #52756+) + **`_AttachmentButton` row** (CL #52719/#52735+, 수정 모드의 첨부 카운트 + Enter 로 modal push) |
| `screens/account_picker.py` | AccountPickerScreen (트리) |
| `screens/tags_picker.py` | TagsPickerScreen + 초성 검색 매칭 |
| `screens/reports.py` | ReportsMenuScreen (11 항목, `t`) + ReportResultScreen — **공식 후잉 MCP 위임** (CL #52755+, `tools/call`) + 빈 결과/에러 안내 (CL #52753+, body 큰 영역) |
| `screens/help.py` | HelpModal (`?`) |
| `screens/attachment_browser.py` | **ModalScreen** (CL #52750+) — 거래내역 위 큰 팝업. Enter 미리보기 (`_AttachmentPreviewModal`, text/PDF), a → FilePicker / p → 경로 / cmd+v paste / d 삭제 / o 외부 viewer / e note. action_add/add_by_path 에 `@work` (CL #52746+) |
| `screens/file_picker.py` | 디렉터리 navigation modal |
| `screens/statement_import.py` | 카드 명세서 import (HTML/CSV/PDF) + dedup |
| `screens/receipt_attach.py` | PDF 영수증 자동 매칭/첨부 + 거래 제안 |
| `screens/tag_management.py` | 태그 rename/merge/delete + 색상 |
| `screens/monthly_entries.py` | 매월입력 (정기) 거래 |
| `screens/budget_edit.py` | 예산 입력/편집 |
| `screens/goal_edit.py` | 장기/월별 목표 입력/편집 |
| `screens/dashboard.py` | DashboardScreen — 한눈 보기 (annotation / attachment / import / hashtags 통계) |
| `theming.tcss` | 전역 스타일 |

### 3.0a core 모듈 인벤토리 (0.1.x)

| 모듈 | 책무 |
| --- | --- |
| `core/db.py` | SQLite **schema v8** (CL #52758+, `entries_cache` 추가) + migrations + annotation/hashtag/tag_meta/import_log/audit_log CRUD |
| **`core/entries_cache.py`** | **신규 (CL #52758+)** entries 영구 sqlite 캐시 layer — `upsert_entries` / `list_cached` / `cached_oldest_date` / `purge_section` |
| **`core/preview.py`** | **신규 (CL #52750+)** 첨부 파일 미리보기 텍스트 추출 — text/* (UTF-8/cp949/latin-1 fallback) + application/pdf (pdfplumber per-page) |
| `core/attachments.py` | sha256 dedup storage + trash + GC + audit |
| `core/dates.py` | KST helper |
| `core/csv_adapters/*` / `core/html_adapters/*` / `core/pdf_adapters/*` | 카드 명세서 어댑터 (현대/하나/신한/국민/삼성) |
| `core/receipt/extractor.py` | PDF 영수증 regex 추출 (date/amount/merchant) |

### 3.1 호출 그래프

```mermaid
flowchart TB
    CLI["CLI<br/>(cli.py — argparse)"]
    TUI["TUI<br/>(app.py + screens/*)"]
    AUTH["load_auth_from_env<br/>(.env / 환경변수)"]
    CLIENT["WhooingClient<br/>(httpx + throttle + retry)"]
    API[("후잉 REST API<br/>https://whooing.com/api")]
    CLI --> AUTH
    TUI --> AUTH
    AUTH --> CLIENT
    CLI --> CLIENT
    TUI --> CLIENT
    CLIENT --> API
```

CLI 와 TUI 는 같은 클라이언트와 같은 SessionState 를 쓰지만 별도 프로세스
경로다. CLI 는 `asyncio.run()` 한 번, TUI 는 Textual 의 이벤트 루프 안에서
`@work` 로 호출.

### 3.2 화면 흐름 (0.17.x 기준)

```mermaid
flowchart TB
    APP[App.on_mount] -->|push| ENTRIES["<b>EntriesScreen</b><br/>(초기 화면)<br/>자체 부팅: sections → accounts → entries"]
    ENTRIES -- "s 키" --> PICKER["SectionPickerScreen<br/>(ModalScreen)"]
    PICKER --> ENTRIES
    ENTRIES -- "a 키" --> ACCOUNTS["<b>AccountsScreen</b><br/>계정과목 조회 / CRUD"]
    ACCOUNTS -- "n / Enter / d" --> ACCDLG["AccountEditDialog<br/>+ ConfirmModal"]
    ACCDLG --> ACCOUNTS
    ACCOUNTS --> ENTRIES
    ENTRIES -- "n / Enter / d" --> ENTDLG["<b>EntryEditDialog</b><br/>(0.12.0+ 폼 전면 개선)"]
    ENTDLG -- "left/right Enter" --> ACCPICK["AccountPickerScreen<br/>(트리, 0.13.0+)"]
    ENTDLG -- "tags Enter" --> TAGPICK["TagsPickerScreen<br/>(추천+자주, 0.13.0+)"]
    ACCPICK --> ENTDLG
    TAGPICK --> ENTDLG
    ENTDLG --> ENTRIES
    ENTRIES -- "t 키 (0.16.0+)" --> RPMENU["ReportsMenuScreen<br/>(드롭다운)"]
    RPMENU --> RPRES["ReportResultScreen<br/>(워커 fetch + JSON)"]
    RPRES --> ENTRIES
    ENTRIES -- "q (Esc 안 됨)" --> EXIT["app.exit + on_unmount<br/>(P4 flush_on_exit)"]
```

**이력**: v0.7.x 까지는 HomeScreen 이 초기 화면이었고 EntriesScreen 은
`e` 키로 push 됐다. CL #51023 에서 사용자 지시로 초기 화면을 EntriesScreen
으로 바꾸고 HomeScreen 제거. 그 후 0.12.0~0.17.x 까지 EntryEditDialog 의
새 위젯 / 트리 picker / tags picker / reports menu 추가.

### 3.3 EntriesScreen 의 인터랙션 모델 (0.14.0~0.17.1)

EntriesScreen 은 다음 직교 상태들을 동시에 가질 수 있다:

| 상태 필드 | 의미 |
|---|---|
| `_show_sentinel: bool` | "[+ 새 거래 추가]" 가시 (CL #51074+) |
| `_column_active: bool` | 노란 cell marker 활성 (CL #51064+) |
| `_active_col: int` | 활성 컬럼 인덱스 (`_COLUMN_NAMES` 인덱스) |
| `_marked_cell: (row, col) \| None` | 마지막 마커링 좌표 (cleanup 용) |
| `_active_filter: ("col", target) \| ("tag", {tag}) \| None` | 활성 필터 (CL #51053+, tag 는 #51106+) |
| `_tag_index: int \| None` | 태그 모드 (item 셀 안의 태그 선택, CL #51106+) |
| `_compact: bool` | 컴팩트 모드 (좁은 터미널, CL #51120+) |
| `_entry_tags: dict[entry_id, list[str]]` | 인라인 표시용 + 태그 필터 source |

**상태 전환 규칙**:
- ←/→ 첫 누름: `_column_active=True` (`_active_col` 그대로).
- ←/→ 이후 누름: `_active_col ± 1` (컴팩트 모드는 hidden 컬럼 skip).
- item 위 → 추가 누름: `_tag_index=0` (태그 모드 진입).
- ↑/↓ 로 row 변경: `_tag_index = None` (자동 종료).
- Esc: `_column_active=False`, `_tag_index=None`, 활성 필터도 해제.
- `c`: 활성 필터만 해제 (marker 유지 — 같은 컬럼 다른 row 재필터 용).
- `r`: 캐시 invalidate + 재로드 → 모든 상태 초기화.

**가로 스크롤 (CL #51121+)**: 컬럼 변경 분기마다
`_scroll_active_col_into_view()` 호출 → `DataTable._get_cell_region(coord)` →
`scroll_to_region(force=True)`.

## 4. 후잉 API 사용 규칙 (본래 mcp-server 와 공유 — wrapper archived 후에도 그대로 유지)

### 4.1 인증

`X-API-Key: <token>` 단일 헤더. 토큰은 절대 로그에 그대로 찍히면 안 된다 —
`WhooingAuth.__repr__` 와 `errors.sanitize_token` 모두 마지막 4자만 hint 로
남기고 나머지는 마스크한다.

### 4.2 엔드포인트 (Phase 1 노출)

| 메서드 | 경로 | 노트 |
| --- | --- | --- |
| GET | `/sections.json` | 섹션 목록 |
| GET | `/accounts.json?section_id=` | 섹션의 계정과목 (type 별 grouping) |
| GET | `/entries.json?section_id=&start_date=&end_date=` | 거래내역 |

### 4.3 응답 포맷

```jsonc
{
  "code": 200 | 204 | 400 | 401 | 402 | 405 | 429 | 5xx,
  "message": "...",
  "results": <list | {key: list} | {id: obj}>,
  "rest_of_api": <int|null>
}
```

- 본문 `code` 가 HTTP status 와 다를 수 있으므로 본문 우선.
- `results` shape 다양성은 `WhooingClient._normalize_collection` 이 흡수.
- `entries.json` 은 server-side 100-cap (`limit` 무시) 이 있어 100건 받으면
  날짜 범위를 bisection 한다 (`_list_entries_chunked`).

### 4.4 에러 매핑

`errors.map_response` (테스트 `test_errors.py`) 한 곳에서:

| code | kind | 비고 |
| --- | --- | --- |
| 400 | USER_INPUT | `error_parameters` 보존 |
| 401 / 405 | AUTH | 토큰 만료/거부 — 재발급 필요 |
| 402 | RATE_LIMIT (일일) | `rest_of_api` 보존, 재시도 안 함 |
| 429 | RATE_LIMIT (분당) | 클라이언트 backoff 재시도 |
| 5xx | UPSTREAM | |
| 그 외 | UPSTREAM | `body_keys` hint |

### 4.5 Rate limit

후잉 한도는 분당 20 / 일일 20,000. 클라이언트는 분당 20 으로 보수적
sliding-window throttle 을 두고, 429 응답엔 1/2/4/8s backoff 로 최대 4회
재시도.

### 4.6 날짜

모든 날짜는 KST 자정 기준 YYYYMMDD. `dates.now_kst()` 가 `Asia/Seoul` 을
강제하므로 호스트 시간대와 무관.

### 4.7 보고서 — 공식 후잉 MCP 위임 (CL #52755+)

보고서/예산/매월 같은 endpoint 는 자체 REST path 추측 (`/report/{account}
.json`) 이 일부 케이스에서 403 으로 실패 (사용자 보고 — CL #52753 진단).
원인은 우리 가정과 후잉 schema 의 차이:

| 우리 옛 가정 | 후잉 실제 schema |
|---|---|
| 종류별 endpoint (`/report/{account}.json`) | **단일 `report-get` 도구 + `type` 파라미터** |
| `account="assets,liabilities"` (콤마 다중) | `account` 가 **enum** (assets/liabilities/capital/expenses/income/all) |
| `budget` 의 `start_date/end_date` 가 YYYYMMDD | **YYYYMM** (6자리) |
| `cashflow` endpoint 없음 (메뉴에서 제거) | `type=cashflow` valid (메뉴엔 아직 안 넣음) |

CL #52755 부터 **`reports.py::_build_menu` 의 11 fetch_fn 이 모두 공식
MCP 위임** (`client.call_official_tool(name, args)` → `official_mcp.py`
의 minimal JSON-RPC client). 자체 REST path 추측 코드 (client.py 의 9
메서드) 는 후방 호환 차원에서 그대로 두되 호출자 없음 — 후속 CL 에서
deprecation 또는 제거.

### 4.8 entries 영구 sqlite 캐시 (CL #52758+)

거래내역의 정확한 모양:
- `entries.json` 의 1년 윈도우 / 100건 cap 정책은 그대로.
- 0.57.0 부터 fetch 결과를 `entries_cache` 테이블 (schema v8) 에 자동 upsert.
- 컬럼 필터 시 (1) 현재 윈도우 → (2) 캐시 lookup → (3) background worker
  로 과거 윈도우 점진 fetch + upsert + 매칭 누적.

캐시 schema (`core/entries_cache.py`):
- PK `(section_id, entry_id)`, 인덱스 `(section, date desc)` / `(section,
  l_account_id)` / `(section, r_account_id)`.
- `raw_json` 으로 후잉 응답 원형 보존 + 정규화 컬럼 (특히 money int)
  우선 노출.
- `_cache_fetched_at` 메타로 미래 TTL 정책 가능 (현재는 X).

invalidation 정책 (현 phase):
- refresh 시 무조건 upsert — 후잉 응답이 최신, 캐시 row 덮어쓰기.
- 사라진 entry 정리 / TTL 은 별도 CL.

## 5. 보안 가드

- 토큰은 `.env` 또는 셸 환경변수에서만 로드. `.gitignore` / Perforce
  ignore 에 모두 들어 있다.
- 응답에 포함될 수 있는 per-section secret (`webhook_token` 등) 은
  `errors.sanitize_for_log` 로 마스크 후 로깅.
- `WHOOING_TUI_CONFIG` 는 절대 경로 override 만 허용 — 상대 경로로 임의
  파일을 읽지 않는다.

## 6. 다음 단계 (Phase 2 진행 상황)

순서는 가치 → 의존도 순.

1. **HomeScreen** — 섹션 picker + 활성 섹션의 계정과목 트리. ✅ CL #50935
   (Phase 2a) 완료. `screens/home.py` 참고.
2. **EntriesScreen** — DataTable, 100-cap footer 인지. ✅ CL #50936
   (Phase 2b) 완료. `screens/entries.py` 참고.
3. **EntryEditDialog** — 거래 추가/수정. ✅ CL #50939 (Phase 2c) 완료.
   `screens/edit_entry.py` + `screens/entries.py` 의 mutation 액션 참고.
   자주입력·매월입력 자동 매칭은 별도 CL 로 분리 (Phase 2d).
4. **POST/PUT/DELETE** 메서드를 `WhooingClient` 에 추가 (CRUD). ✅
   CL #50939 와 함께. `client.py` 의 `_request` / `create_entry` /
   `update_entry` / `delete_entry`. 후잉 REST 의 정확한 mutation path 는
   라이브 검증 후 조정 — RESTful 가정으로 시작.
5. **로컬 캐시 (sqlite)** — accounts/entries 의 inter-session cache.
   mcp-server 의 `whooing-data.sqlite` 와는 분리된 별도 db.
   ✅ Phase 3 완료. 코드 변경은 CL #50943 (monorepo CL B) 에 흡수,
   문서 정리는 CL #50940. `cache.py` (CacheStore) +
   `client.py::CachedWhooingClient` + `config.py` `[cache]` 섹션 +
   화면의 `invalidate_section` 호출 + `whooing-tui.toml.example`.
6. **MCP 직접 호출** — Phase 4. 보고서 화면에서 정식 채택 (CL #52755+).
   - CL #50987 — scaffolding (`mcp_bridge.py::WhooingMcpBridge`) 추가.
   - CL #51007 — archived `whooing_mcp.official_mcp` 의존 제거, 자체
     HTTP JSON-RPC 클라이언트로 재작성.
   - CL #51008 — `mcp_bridge.py` 제거 (당시 stale 우려).
   - **CL #52755 — `official_mcp.py` 재도입**. 자체 REST path 추측의 한계
     (보고서 endpoint 403 — `account` enum / 콤마 다중 X) 발견 → 공식 후잉
     MCP server 의 schema 가 정답. `OfficialMcpClient` (SSE 지원 포함) +
     `WhooingClient.call_official_tool` helper. `reports.py::_build_menu`
     의 11 fetch_fn 모두 `tools/call` 위임.
7. **점진적 entries 캐시 + 필터 확장** — Phase 5 (CL #52758+, 0.57.0).
   - schema v8 `entries_cache` 테이블 + `core/entries_cache.py` layer.
   - `refresh_entries` 가 fetch 결과를 매번 upsert (영구 축적).
   - `_apply_filter` 가 (1) 현재 윈도우 → (2) sqlite 캐시 → (3) background
     worker 가 `WHOOING_FILTER_EXPAND_MONTHS` (default `3,6,12,24`) step 으로
     과거 fetch + upsert + 매칭 누적. `_filter_epoch` 카운터로 race 방지.
   - 보류 (별도 CL): cache invalidation (사라진 entry 정리), 부팅 시 캐시
     우선 표시, per-section TTL.
8. **graceful quit** — Phase 6 (CL #52761+, 0.59.0).
   - `q` action `quit → graceful_quit`. `_ShutdownModal` 표시 + thread
     executor 로 `flush_on_exit` 실행 → 끝나면 `self.exit()`. CLI 에 응답
     없는 구간 사라짐. `ctrl+c` 는 강제 종료 path 유지.
9. **메뉴바 확장** — Phase 7 (CL #52759+, 0.58.0).
   - F10 외에 Alt 단독 / Alt+M / Alt+F 진입.
   - MenuBar 마우스 클릭 → 클릭한 메뉴부터 popup (`MenuClicked` Message
     + `menubar_index_at_offset`).
10. **거래 row context menu** — Phase 8 (CL #52763+, 0.60.0).
    - `m` (또는 `ㅡ`) — 선택 거래의 popup 메뉴 (수정/삭제/첨부/새 거래,
      multi-select 활성 시 일괄 태그).
    - 기존 `MenuPopup` 재사용 — OptionList 기반.

### 6.1 Phase 2a (HomeScreen) 메모 — 후속 화면 구현 시 재사용할 결정들

- **워커 그룹 분리**: `@work(exclusive=True, group="sections")` /
  `group="accounts"` 로 sections 와 accounts 호출을 별도 직렬화.
  EntriesScreen 의 entries 호출도 같은 패턴 (`group="entries"`) 으로 가면
  여러 화면이 공존할 때 worker 간섭이 없다.
- **상태 공유**: 화면 간 상태는 `App.session` (SessionState) 하나로만
  주고받는다. HomeScreen 은 `self._sections_by_id` 같은 화면-로컬 캐시도
  갖지만 source-of-truth 는 SessionState — 새 화면도 같은 규칙.
- **에러 표면화**: 각 Screen 이 자체 status bar (Static, id="status") 를
  갖고 `error` CSS 클래스로 시각 강조. Phase 2 의 다른 화면도 같은
  관용을 따르면 사용자 mental model 이 일관된다.
- **테스트 친화 주입**: `WhooingTuiApp(client=...)` 로 client 를 주입,
  테스트는 `FakeClient` 로 대체. 새 화면을 만들 때도 client 를 직접 새로
  만들지 말고 `app._client` (또는 화면 생성자 인자) 를 통해서만 받는다.
- **자동 활성화 UX**: HomeScreen 은 첫 섹션을 자동으로 활성화해 accounts
  까지 로드한다. EntriesScreen 으로 push 할 때도 같은 패턴 — 진입과 동시에
  최근 N일 (config.entries.default_window_days) 거래를 자동 fetch.

## 7. 로컬 sqlite + P4 자동 동기화 (0.15.0+, CL #51114+)

### 7.1 데이터 위치

| 우선순위 | 경로 | 사용 |
|---|---|---|
| 1 | `$WHOOING_DATA_DIR` | 명시 override (테스트 격리, 사용자 임의 경로) |
| 2 | `<project_root>/db/` | monorepo 안에서 실행 시 |
| 3 | `~/.whooing/` | pip install / monorepo 외 fallback |

`<project>/db/whooing-data.sqlite` 는 P4 control 에 binary 로 등록.
`.gitignore` 의 `*.sqlite` 가 git mirror 푸시는 차단해 사용자 데이터가
public GitHub 에 안 올라간다.

### 7.2 자동 동기화 흐름

```mermaid
flowchart TB
    S[App start] --> ENV{$WHOOING_DATA_DIR<br/>set?}
    ENV -->|Yes 테스트| IS[init_schema]
    ENV -->|No 실 사용자| MIG[_maybe_migrate_legacy_db<br/>~/.whooing → project/db]
    MIG --> SY[p4_sync.sync_db_from_p4<br/>p4 sync db]
    SY --> IS
    IS --> RUN[App 실행 + 사용자 mutation]
    RUN -->|_persist_local / _purge_local| AS["submit_db_to_p4<br/>(Thread daemon=False)<br/>_PENDING 추적"]
    AS -->|p4 reconcile -e -a -d| RC[변경 detect]
    RC -->|p4 submit -d desc| OK[P4 head 갱신]
    RUN --> EX[App exit / on_unmount]
    EX --> WP[wait_for_pending<br/>모든 worker join]
    WP --> FE[flush_on_exit<br/>blocking submit 안전망]
    FE --> Q[프로세스 종료]
```

### 7.3 mechanical description

`p4_sync.describe_annotation` 이 LLM 미관여 mechanical 한 줄을 빌드:

| mutation | description |
|---|---|
| memo 만 변경 | `[whooing-tui] entry e123 memo upsert` |
| 해시태그 만 변경 | `[whooing-tui] entry e123 hashtags set [식비, 커피]` |
| 둘 다 | `[whooing-tui] entry e123 memo upsert; hashtags set [식비, 커피]` |
| 거래 삭제 | `[whooing-tui] entry e123 deleted` |

flush_on_exit 의 fallback description 은 `[whooing-tui] session end —
flush pending db changes`.

### 7.4 silent 정책

P4 환경 부재 / 매핑 외 / 통신 실패 등 어떤 단계에서든 사용자 표면화 X
(사용자 명시 요구사항). 로그 (`log.warning`/`debug`) 까지만. 종료 흐름은
타임아웃 30s/스레드로 무한 차단 방지.

## 8. 좁은 터미널 적응 (0.17.0+, CL #51120+)

iPhone Blink 등 ~40-50 cells 환경 우선 대응.

### 8.1 모달 반응형 width (Phase 1)

12 모달 적용 패턴:

```css
width: 95%;
max-width: <기존 N>;   /* 76 / 64 / 60 / 56 / ... */
min-width: 30;
```

좁은 터미널 (40 cells) → 95% (38 cells) 로 축소, 단 30 미만 X.
넓은 터미널 (120 cells) → max-width 로 cap (필요 이상 안 늘어남).

### 8.2 EntriesScreen 컴팩트 모드 (Phase 2)

| 임계값 | 모드 | 보이는 컬럼 |
|---|---|---|
| width < 60 | `_compact = True` | date / money / item (3개) |
| width ≥ 60 | `_compact = False` | date / money / left=12 / right / item / memo (6개) |

구현:
- `on_resize(event)` 가 임계값 변화 감지 → `_compact` 토글 +
  `_apply_column_widths_for_size()` 가 left(2) / right(3) / memo(5) 의
  width=0 처리.
- `_COLUMN_NAMES` 의 인덱스 정의는 유지 — 네비/marker 코드 안 깨짐.
- ←/→ 네비가 `_next_visible_col` 로 hidden 컬럼 자동 skip (CL #51121+).
- 모든 컬럼 변경 분기에서 `_scroll_active_col_into_view()` 호출 →
  `DataTable._get_cell_region` + `scroll_to_region(force=True)` 으로
  활성 cell 을 가시 영역 안에.

## 9. 테스트 전략

- 단위: `auth` / `dates` / `errors` / `state` / `config` / `filters` /
  `ime` / `data` / `p4_sync` — 외부 의존 없이.
- 클라이언트: `respx` 로 후잉 REST 응답 모킹. 실 토큰 없이도 통과.
- TUI: Textual `App.run_test()` 기반 스냅샷 + 키 입력 시뮬.
- **좁은 터미널 회귀**: `App.run_test(size=(40, 30))` / `(60, 30)` /
  `(120, 30)` 으로 폭별 동작 검증 (`tests/test_narrow_terminal.py`).
- **테스트 격리**: `conftest.py` 의 `_isolated_user_state` autouse fixture
  가 `WHOOING_DATA_DIR` / `XDG_CONFIG_HOME` 을 tmp 로. P4 sync / flush 도
  env 미설정 시만 동작하므로 테스트가 실 P4 상태 안 끌어옴.
- **p4_sync 모킹**: `WHOOING_P4_BIN` 환경변수로 `p4` 바이너리를 fake shell
  스크립트로 교체 — subprocess 호출 시퀀스를 log 파일로 기록해 검증.
- 라이브 smoke 테스트: 의도적으로 빠짐. archived `mcp/tests/_live_smoke.py`
  가 실 후잉 동작은 검증해뒀다.

**현재 통계** (0.17.1 기준): tui + core 합 **471 passed**.

## 10. 다음 세션 / Phase 7+ 후보

본 문서는 0.17.1 까지를 반영. 다음 작업은 사용자 우선순위에 따라:

- **보고서 화면 종류별 렌더러** (Phase 2 of CL #51116) — 현재 raw JSON
  pretty dump 가 baseline. 자산표 / 시계열 표 / 캘린더 그리드 / 예산
  진행률 바 등 종류별 전용 렌더러로 점진 교체.
- **컴팩트 모드 사용자 경험 보강** — money 부호 (-/+) 표시, 인라인 태그
  의 컴팩트 압축 (limit 1 + `…(N)`), 사용자 토글 (수동 컴팩트 on/off).
- **iPhone Blink 외 모바일 환경** — Termius / Termius / Prompt 3 등 다른
  iOS 터미널 회귀 검증.
- **자주입력 / 매월입력 매칭** (Phase 2d, 미완) — EntryEditDialog 진입 시
  후잉의 frequent_items / monthly_items 와 매칭해 자동 prefill.
- **첨부파일 import 흐름 통합** — Phase 6 의 `attachment_browser` /
  `statement_import` 가 EntriesScreen 과 자연 연결되도록.

세션 컨텍스트는 `MEMORY.md` (P4-only) 의 §8 변경 이력 + 본 문서의 §3.0
모듈 인벤토리 + `CHANGELOG.md` 의 CL 별 상세를 함께 보면 복원 가능.
