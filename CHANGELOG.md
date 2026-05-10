# whooing-tui — 변경 이력

각 항목은 Perforce CL 단위로 끊는다.

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
