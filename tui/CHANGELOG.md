# whooing-tui — 변경 이력

각 항목은 Perforce CL 단위로 끊는다.

## CL #50940 — 0.5.0 — Phase 3 문서 정리 (cache 통합 사실 기록) (2026-05-10)

**경위**: Phase 3 (sqlite 캐시) 의 코드 변경 (cache.py / test_cache.py /
config.py / app.py / screens 의 invalidate 통합) 은 monorepo 전환 중에
다른 작업의 CL #50943 (monorepo CL B) 에 흡수되어 submit 됐다 — p4 move
중 우리 새 파일도 함께 옮겨졌기 때문. 코드는 정상 위치 (`tui/`) 에 들어가
있고 통합 테스트도 12개 모두 통과하지만, `CHANGELOG.md` / `DESIGN.md` /
`MEMORY.md` 의 항목 갱신은 빠진 채라 본 CL 이 그 누락분만 보충한다.

### 수정

- `CHANGELOG.md` — 본 항목 (Phase 3) 추가.
- `DESIGN.md` §6 — sqlite 캐시 (5번) 를 ✅ CL #50943 흡수 표시.
- `MEMORY.md` §6 / §8 — Phase 3 진행 사실 + monorepo 전환의 영향 기록.
- `pyproject.toml` + `__init__.py` — 0.4.0 → 0.5.0.

### Phase 3 의 실제 코드 변경 (CL #50943 에 흡수, 2026-05-10)

- `tui/src/whooing_tui/cache.py` — `CacheStore` (sqlite-backed inter-session
  store). accounts TTL 1시간 / entries TTL 5분, mutation 시 invalidate.
  `:memory:` 도 지원 (테스트). `default_cache_path()` 가
  `.whooing-tui-cache/whooing-tui.sqlite` 반환 (gitignore 차단).
- `tui/src/whooing_tui/client.py::CachedWhooingClient` — `WhooingClient` 와
  같은 인터페이스의 wrapper. accounts/entries cache, mutation 시
  invalidate, `invalidate_section(section_id)` public.
- `tui/src/whooing_tui/config.py` — `[cache]` 섹션 (`enabled` /
  `accounts_ttl_sec` / `entries_ttl_sec`).
- `tui/src/whooing_tui/app.py::run_app` — `cfg.cache_enabled` 면 sqlite
  캐시 wrapper 빌드해 주입.
- `tui/src/whooing_tui/screens/home.py` / `entries.py` — `r` 액션이
  `invalidate_section()` 을 callable 로 발견 시 강제 invalidate 후 fetch.
- `tui/whooing-tui.toml.example` — `[cache]` 옵션 문서화.
- `tui/tests/test_cache.py` — 12 cases (CacheStore 단위 + CachedWhooingClient
  통합).

검증: monorepo 전체 `make test` → core 72 + tui 94 = **166 passed**.

### 의도적 누락 (다음 CL 로)

- 자주입력·매월입력 매칭 (`frequent_items` / `monthly_items`).
- 후잉 공식 MCP 직접 호출 (Phase 4).
- 화면 도움말 모달 / 콘솔 스크립트 검증 / coverage / `.env` 공통 위치.

## CL #50939 — 0.4.0 — Phase 2c: EntryEditDialog + WhooingClient CRUD (2026-05-10)

후잉 거래의 추가/수정/삭제. 이 CL 부터 mcp-server-wrapper 와 정책 분기 —
wrapper 는 read-only 로 mutating 을 공식 MCP 에 위임하지만, 본 TUI 는
사용자가 직접 키보드로 다루는 도구라 후잉 REST 의 mutation endpoint 를
직접 호출한다.

### 추가

- `src/whooing_tui/screens/edit_entry.py`
  - `EntryEditDialog(ModalScreen[EntryDraft|None])` — 거래 추가/수정 폼.
    필드 6개 (date / money / left / right / item / memo). `Ctrl+S` 저장,
    `Esc` 취소.
    * `_resolve_account()` — `account_id` 직접 입력 또는 표시명(한국어/
      영문, 대소문자 무시, 양 끝 공백 허용) 양쪽 매칭.
    * `_strip_comma_int()` — 천단위 콤마 입력 허용.
    * 검증: YYYYMMDD, money 양수, left ≠ right, account 매칭.
    * 수정 모드는 `existing` dict 로 prefill + `entry_id` 보존.
  - `ConfirmModal(ModalScreen[bool])` — 짧은 yes/no 모달. 삭제처럼 되돌릴
    수 없는 액션 직전. `y`/`n` 키와 button 둘 다.
- `tests/test_edit_entry_dialog.py` — 폼 검증 단위 테스트 8 cases
  (`_strip_comma_int`, `_resolve_account` by id/title/case-insensitive/
  unknown, dataclass instantiation).
- `tests/test_client_mutations.py` — `respx` 7 cases:
  * create_entry POST body 정확성 + optional 필드 omit.
  * update_entry PUT 변경 필드만 (None 은 omit — 덮어쓰기 방지).
  * delete_entry DELETE + section_id 쿼리.
  * 400 → USER_INPUT, 401 → AUTH 매핑.
  * `_coerce_dict` variants.
- `tests/test_entries_mutate.py` — App.run_test() 통합 5 cases:
  * `n` (new) → dialog → dismiss(EntryDraft) → create_entry 호출 →
    SessionState 의 type 으로 보강된 body. 재로드 후 row count 증가.
  * `enter` (edit) → dialog 가 선택된 row prefill + entry_id → update_entry.
  * `d` (delete) → ConfirmModal → No 면 호출 안 함, Yes 면 delete_entry.
  * create 실패 시 status error.
  * 빈 entries 에서 delete → "선택된 거래 없음" 안내.

### 수정

- `src/whooing_tui/client.py`
  - `_request(method, path, ...)` 공통 HTTP 호출 — throttle / 429
    backoff / 응답 매핑을 GET 외 POST/PUT/DELETE 도 사용하도록 추출.
  - `_get` / `_post` / `_put` / `_delete` 단순 wrapper.
  - **`create_entry` / `update_entry` / `delete_entry`** — 후잉 공식 MCP
    의 `entries-create/update/delete` schema 와 동일한 입력 필드.
    RESTful 가정으로 `POST /entries.json`, `PUT /entries/<id>.json`,
    `DELETE /entries/<id>.json?section_id=` 호출. 라이브 검증에서 path
    가 다르면 `_ENTRIES_PATH` / `_entry_path()` 만 조정 가능.
  - `_coerce_dict()` — mutation 응답이 dict / list[dict] / 그 외 어떤
    형태로 와도 dict 1개로 정규화 (보수적 처리).
- `src/whooing_tui/screens/entries.py`
  - 키 바인딩: `n` (New), `enter` (Edit), `d` (Delete) — 모두 priority.
  - `_entries: list[dict]` — DataTable row index ↔ entry 1:1 매핑으로
    선택된 거래의 entry_id 추적.
  - `action_new_entry` / `action_edit_entry` / `action_delete_entry` +
    `_submit_create` / `_submit_update` / `_submit_delete` (worker group
    `mutate`). 성공 시 `refresh_entries` 자동 호출. 실패 시 status error.
  - `_account_type()` — SessionState.flat 에서 type 조회.
- `CHANGELOG.md` / `DESIGN.md` / `MEMORY.md` — Phase 2c 진행 상황.
- `pyproject.toml` + `src/whooing_tui/__init__.py` — 0.3.0 → 0.4.0.

### 검증

  make test    82 passed in 5.24s
               (Phase 1 48 + 2a 6 + 2b 6 + 2c 22)

  라이브 검증은 사용자 수동으로 미룸 (분당 20 한도 부담 회피). `make run`
  → 'n' 키로 테스트 섹션(s133178) 에 작은 거래 입력 시도. 후잉 REST 의
  실 path 가 RESTful 가정과 다르면 `WhooingClient._ENTRIES_PATH` /
  `_entry_path()` 만 조정.

### 정책 분기 — mcp-server-wrapper 와의 차이

- mcp-server-wrapper: read-only. 거래 mutation 은 후잉 공식 MCP 의
  `entries-create/update/delete` 에 위임 (`tools/delete.py` 의 chained
  call).
- whooing-tui (본 도구): 사용자가 직접 키보드로 다룰 때 latency / 의존성
  부담을 줄이기 위해 후잉 REST 를 직접 호출.

같은 후잉 토큰을 공유하므로 한쪽 한도를 다른 쪽이 갉아먹는다 — 두 도구를
동시에 사용할 때는 분당 20 한도가 합산임을 인지.

### 의도적 누락 (다음 CL 로)

- 자주입력·매월입력 매칭 (frequent_items.list / monthly_items.list).
- 로컬 sqlite 캐시.
- 라이브 검증 (사용자 수동).

## CL #50937 — 문서 다이어그램을 mermaid 로 마이그레이션 (2026-05-10)

DESIGN.md §2 자매 도구 관계 + §3.1 호출 그래프 ASCII → mermaid flowchart.
§3 디렉토리 트리는 markdown 표로. MEMORY.md §10 — 다이어그램 가이드라인
보존 (사용자 지시 2026-05-10).

## CL #50936 — 0.3.0 — Phase 2b: EntriesScreen + 100-cap footer (2026-05-10)

### 추가

- `src/whooing_tui/screens/entries.py` — EntriesScreen.
  - `DataTable` 컬럼: date / money / left / right / item / memo.
  - 진입 시 최근 N일 (`config.entries.default_window_days`, 기본 30)
    자동 fetch.
  - account_id 는 `SessionState.title_of()` 로 즉시 표시명으로 변환 —
    사용자에게는 코드 대신 이름.
  - money 는 천단위 콤마 (`_fmt_money` — None / 빈 값 안전).
  - 정렬: `entry_date desc`, 동일 일자는 `entry_id desc` 보조.
  - **100-cap 인지 footer**: 같은 entry_date 가 정확히 100건이면 누락
    가능성 의심 → status bar 의 `warn` 클래스 + `last_cap_warning=True`.
    일자 목록을 메시지에 노출 (앞 3개 + "…").
  - 키 바인딩: `q`/`escape` (Back), `r` (Refresh), `+` (윈도우 +7일,
    max 5년), `-` (-7일, min 1일).
- `tests/test_entries_screen.py` — `App.run_test()` 통합 6 cases:
  - HomeScreen → `e` → EntriesScreen push + entries 자동 로드.
  - account_id → title 변환, money 콤마 포맷.
  - `q` 로 HomeScreen 복귀.
  - `+` 가 윈도우 확장 + 재로드 (start_date 가 더 이른 날짜).
  - 단일 일자에 100건 = warn 클래스 + 메시지.
  - 후잉 ToolError 시 error 클래스 + 0행.
  - 빈 sections 상태에서 `action_open_entries` 가 push 거부 + status
    error.

### 수정

- `src/whooing_tui/screens/home.py` — `e` 키 바인딩 + `action_open_entries`.
  - `Binding(priority=True)` — OptionList / Tree focus 일 때도 화면
    레벨 액션이 우선이도록.
  - 활성 섹션 없으면 status error 로 안내, push 거부.
  - 지연 import (`from .entries import EntriesScreen`) 로 패키지 초기화
    분리.
- `CHANGELOG.md` / `DESIGN.md` / `MEMORY.md` — Phase 2b 진행 상황 갱신.
- `pyproject.toml` + `src/whooing_tui/__init__.py` — 0.2.0 → 0.3.0.

### 검증

  make test    60 passed in 2.55s (Phase 1 48 + HomeScreen 6 + EntriesScreen 6)

### 의도적 누락 (다음 CL 로)

- EntryEditDialog (거래 추가/수정) + WhooingClient 의 POST/PUT/DELETE.
- 로컬 sqlite 캐시.
- 화면 도움말 (`?` 모달).
- 날짜 범위 직접 입력 dialog (`d` 키 — bindings 자리 잡힘, dialog 미구현).

### 학습된 함정

- `DataTable.column_count` 속성은 textual 8.x 에 없음 — `len(table.columns)`
  사용. 후속 화면 테스트에 동일 적용.
- `default = sections or [...]` 패턴은 빈 리스트도 falsy 로 잡아 default
  로 덮어버림 → `is None` 분기 필수. 후속 FakeClient 재사용 시 주의.
- OptionList / Tree 안에서 화면 레벨 단축키를 잡으려면
  `Binding(priority=True)` 필요. Phase 2a 의 `r` 도 함께 priority=True 로
  승격해 두었다.

## CL #50935 — 0.2.0 — Phase 2a: HomeScreen (2026-05-10)

### 추가

- `src/whooing_tui/screens/__init__.py` — Textual Screen 패키지.
- `src/whooing_tui/screens/home.py` — HomeScreen.
  - 좌: 섹션 picker (`OptionList`, 키보드 navigation + enter 선택).
  - 우: 활성 섹션의 계정과목 트리 (`Tree`, type 별 그룹: 자산/부채/자본/
    수입/지출/그룹).
  - 첫 mount 시 `sections-list` 자동 호출 + 첫 섹션 자동 활성화 → 사용자
    액션 없이도 화면이 의미있게 채워진다.
  - `@work(exclusive=True)` 로 sections / accounts 호출을 그룹별 직렬화 —
    빠른 섹션 전환 시 마지막 1개만 결과 적용.
  - `r` (refresh): 활성 섹션의 accounts 재로드, 섹션 미선택이면 sections
    재로드.
  - 후잉 ToolError 발생 시 화면 하단 status bar 가 error 클래스로 표시
    (모달 없음 — Phase 2a 는 화면 1개로 단순 유지).
  - `HomeScreen.last_status` 가 마지막 메시지를 평문으로 보관 (테스트가
    Static 의 사적 API 에 의존하지 않도록).
- `tests/test_home_screen.py` — Textual `App.run_test()` 기반 통합
  테스트 6 cases. `FakeClient` 로 실 후잉 호출 없이:
  - 첫 mount 후 sections + accounts 자동 로드.
  - 다른 섹션 선택 시 SessionState 갱신 + accounts 재호출 + 이전 섹션
    캐시 무효화.
  - sections / accounts 에러 시 status bar 의 error 클래스.
  - 빈 sections 시 picker 가 disabled placeholder 1개만.
  - `r` action 이 활성 섹션의 accounts 만 재로드 (sections 재호출 안 함).

### 수정

- `src/whooing_tui/app.py` — Phase 1 placeholder 제거.
  - `WhooingTuiApp.__init__(client=...)` 로 의존성 주입 (테스트 친화).
  - `self.session = SessionState()` — 단일 세션 상태가 모든 화면에 노출.
  - `on_mount` 에서 `HomeScreen` push.
  - `run_app()` 가 진입 시 `load_auth_from_env()` 검증 후 GUI 실행 — 토큰
    문제 시 stderr 안내 + return 3 (TUI 에서 모달 띄우는 것보다 즉시
    .env 를 고치게 만든다).
- `src/whooing_tui/theming.tcss` — Header/Footer 의 dock + 색 미세조정
  (HomeScreen DEFAULT_CSS 가 화면 디테일을 책임지므로 본 파일은 전역
  최소만).
- `tests/test_auth.py::test_load_auth_from_env_missing` — `monkeypatch.
  setenv("WHOOING_AI_TOKEN", "")` 로 변경. dotenv 의 자동 로드가 monkeypatch.
  delenv 직후 .env 의 토큰을 다시 채우는 문제를 회피 (load_dotenv 는
  override=False 가 기본이라 빈 문자열은 유지된다).

### 검증

  make test           54 passed in 0.98s (Phase 1 48 + HomeScreen 6)
  make run            HomeScreen 정상 mount + sections/accounts 로드 (육안 확인)

### 의도적 누락 (다음 CL 로 이연)

- EntriesScreen — HomeScreen 에서 enter 두 번에 push 되는 거래내역 표.
- EntryEditDialog — 거래 추가/수정. WhooingClient 의 POST/PUT/DELETE 동시.
- 로컬 sqlite 캐시.

## CL #1 — 0.1.0 — Phase 1 골격 (2026-05-10)

### 추가

- 프로젝트 메타파일: `pyproject.toml` (hatchling, textual≥0.50, httpx,
  pydantic, python-dotenv), `Makefile` (install/test/run/sections/clean),
  `.gitignore`, `.env.example`, `whooing-tui.toml.example`, `LICENSE` (MIT).
- 패키지 `src/whooing_tui/`:
  - `auth.py` — `WhooingAuth` (X-API-Key, 마지막 4자 마스크) +
    `load_auth_from_env`.
  - `errors.py` — HTTP code → ToolError(USER_INPUT/AUTH/RATE_LIMIT/UPSTREAM)
    매핑, `sanitize_for_log` 로 webhook_token 등 비밀 키 마스크.
  - `dates.py` — KST(Asia/Seoul) 강제 YYYYMMDD/YYYYMM 파서, 1년 단위 분할.
  - `models.py` — Pydantic `Section` / `Account` / `Entry` (extra=allow) +
    `ToolError`.
  - `client.py` — httpx 기반 `WhooingClient`. 분당 20 sliding-window
    throttle, 429 backoff (1/2/4/8s, max 4회), 100-cap entries bisection,
    응답 shape (list / {key: list} / {id: obj}) 정규화.
  - `config.py` — TOML `[ui]` / `[entries]` 로더, `$WHOOING_TUI_CONFIG`
    override.
  - `state.py` — `SessionState` (활성 섹션 + 계정 양방향 인덱스).
  - `cli.py` — `whooing-tui {sections|accounts|entries} list` 헤드리스
    서브커맨드, `--json` / `-v` / `--section` / `--days` / `--start --end`,
    종료 코드 (0/2/3/4/5/6).
  - `app.py` + `theming.tcss` — Textual App 자리표시자 (`q` quit, `t`
    theme toggle). Phase 2 에서 실제 화면으로 대체.
  - `__main__.py` — `python -m whooing_tui` 진입점.
- 테스트 `tests/`:
  - `test_auth.py` — 토큰 마스크, env 로딩.
  - `test_dates.py` — KST 포맷, 1년 분할, 잘못된 입력.
  - `test_errors.py` — 응답 코드 매핑, secret sanitize.
  - `test_config.py` — TOML 로딩, 부분 override, malformed fallback.
  - `test_state.py` — 섹션 전환 시 계정 캐시 무효화.
  - `test_client.py` — `respx` 로 sections / accounts / entries / 401 / 400.
- 문서: `README.md` (한국어 quickstart), `DESIGN.md` (아키텍처·후잉 API
  규칙), 본 `CHANGELOG.md`.

### 의도적 누락 (Phase 2 로 이연)

- HomeScreen / EntriesScreen / EntryEditDialog.
- POST/PUT/DELETE 메서드 (`WhooingClient` 는 GET 만).
- 로컬 sqlite 캐시.
- 자주입력·매월입력 매칭.

### 설계 결정 메모

- whooing-mcp-server-wrapper 와 코드 중복: 의존성·릴리즈 사이클을 분리하기
  위함. `auth.py` / `dates.py` / `errors.py` 는 거의 동일하게 인용.
- `client.py` 는 mcp-server 쪽의 `read-only` 경로만 가져왔다. Phase 2 에서
  CRUD 확장 예정.
- TUI 가 아직 placeholder 인 이유: 헤드리스 CLI 로도 즉시 가치를 주고,
  Phase 2 의 화면 작업을 별도 CL 로 깨끗이 떨어트리기 위해.
