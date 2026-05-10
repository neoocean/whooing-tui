# whooing-tui — 변경 이력

각 항목은 Perforce CL 단위로 끊는다.

## CL #51013 — 0.7.7 — `whooing.py` 자동 re-exec 패턴 (시스템 python 으로 호출돼도 동작) (2026-05-10)

0.7.6 의 `whooing.py` 가 시스템 `python3` 으로 호출되면 `httpx` /
`pydantic` / `dotenv` 가 없다고 실패하던 문제를 해결. **시스템 python 으로
호출 → 자동으로 monorepo `.venv/bin/python` 으로 re-exec** 하는 패턴.

### 수정

- `whooing.py`
  * `_can_import_deps()` — 외부 deps 가 현재 인터프리터에서 import
    가능한지 빠르게 검사 (시스템 python 에 deps 가 의도적으로 깔린
    환경 포용).
  * `_reexec_in_venv_if_needed()` — venv 안이 아니고 deps 도 없으면
    `os.execv(_VENV_PY, ...)` 으로 본 프로세스를 venv python 으로 교체.
    venv 가 없으면 stderr 에 `make install` 안내 + exit 3.
  * **`_running_in_venv()` 버그 수정** — 기존 구현은 `sys.executable`
    의 realpath 를 비교했는데 venv 의 python 이 시스템 python 의
    symlink 인 macOS / Linux 환경에서 양쪽이 같은 binary 로 풀려 venv
    구분이 안 됐다. 표준 마커인 `sys.prefix` (venv 활성화 시 venv root
    을 가리킴) 를 `.venv` 디렉토리와 비교하도록 교체. `pyvenv.cfg` 가
    설정하는 값이라 신뢰 가능.
  * 모듈 docstring 에 자동 re-exec 동작 명시.
- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.7.6 → 0.7.7.

### 검증

- `python3 whooing.py --help` (시스템 python3) → 자동 re-exec, help 정상.
- `python3 whooing.py sections list` (시스템 python3) → 자동 re-exec, 실
  후잉 응답 정상.
- `.venv/bin/python whooing.py --help` (이미 venv) → re-exec skip, 그대로 동작.
- `make smoke-cli` → 진입점 3 종 모두 동작.

### 학습된 함정

`os.path.realpath(sys.executable)` 로 venv 감지하는 패턴은 macOS framework
build 환경에서 작동 안 함 — `.venv/bin/python` 이 시스템 python 의
symlink 라 둘 다 `python3.X` 로 풀린다. **표준 venv 마커는 `sys.prefix !=
sys.base_prefix`** 또는 `sys.prefix == <venv_dir>`. 후속 venv 감지 코드도
같은 패턴을 따를 것.

## CL #51009 — 0.7.6 — monorepo 루트 진입점 `whooing.py` 추가 (2026-05-10)

`python whooing.py [args]` 가 `python -m whooing_tui [args]` / 콘솔
스크립트 `whooing-tui` 와 100% 동등. 사용자가 monorepo 루트에서 짧게
실행할 수 있는 발견 가능한 진입점.

### 추가

- `whooing.py` (monorepo 루트, +x permission)
  * `tui/src` 와 `core/src` 를 `sys.path` 에 prepend → venv 활성화 안
    된 상태에서도 외부 deps 만 있으면 동작.
  * 지연 import 후 `whooing_tui.cli.main()` 으로 그대로 위임. 자체 로직
    없음 — 같은 코드 경로 재사용.
  * 모듈 docstring 에 사용 예시 + 전제 (deps 위치) 명시.

### 수정

- `Makefile` — `smoke-cli` 가 진입점 **3 종** (python -m / 콘솔 스크립트
  / `whooing.py`) 을 모두 검증. 하나라도 깨지면 즉시 발견.
- `README.md` (monorepo root) — 빠른 시작 섹션의 TUI 실행 방법을 3가지
  동등 형태로 정리. 디렉터리 레이아웃에 `whooing.py` 추가.
- `tui/README.md` — 빠른 시작의 헤드리스 CLI 예시에 `whooing.py` 추가.
- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.7.5 → 0.7.6.

### 검증

- `make smoke-cli` → "OK — 진입점 3 종 모두 동작."
- `.venv/bin/python whooing.py sections list` → 실 후잉 응답 (Default,
  테스트 두 섹션) 정상.
- `make test-tui` → 170 passed (회귀 없음).

### Perforce 표시

- `whooing.py` 는 `p4 add -t text+x` 로 등록 — 다른 사용자가 sync 받을
  때도 +x permission 보존. shebang `#!/usr/bin/env python3` 가 동작하면
  `./whooing.py` 도 가능 (단, 권장은 `python whooing.py` 명시 호출 —
  외부 deps 의 venv 위치를 사용자가 명확히 통제).

## CL #51008 — 0.7.5 — `mcp_bridge.py` 제거 (UI 통합 미완성, unused 코드 정리) (2026-05-10)

CL #50987 (scaffolding) → #51007 (archived 의존 제거 + 자체 클라이언트로
재작성) 까지 두 단계를 거쳤지만 본 모듈을 호출하는 화면이 한 곳도 없다.
unused 코드를 길게 유지하면 의도가 불분명해지므로 정리. 미래에 보고서 /
예산 / 자주입력 매칭 같은 화면이 추가될 때 본 모듈을 새로 만들 수 있도록
`mcp/` 디렉토리의 `OfficialMcpClient` (archived) 와 CL #51007 의 자체 구현
은 git/Perforce history 에서 그대로 참조 가능.

### 제거

- `tui/src/whooing_tui/mcp_bridge.py` — `WhooingMcpBridge`.
- `tui/tests/test_mcp_bridge.py` — 12 cases.

### 수정

- `tui/DESIGN.md` §6 의 6번 (MCP 직접 호출) — 진행 이력 (#50987 →
  #51007 → 본 #51008) 명시 + 미래 reactivation 후보 표기.
- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.7.4 → 0.7.5.

### 검증

- `make test-tui` → 170 passed (이전 182 - 12 cases = 170).

### 미래 reactivation 가이드

후속 CL 에서 MCP 직접 호출이 필요해지면:

1. **history 참조**: `p4 print -q //woojinkim/scripts/whooing-tui/tui/src/whooing_tui/mcp_bridge.py#3` 또는 `git show 53e6431:tui/src/whooing_tui/mcp_bridge.py`. 본 CL 직전의 자체 HTTP JSON-RPC 클라이언트가 그대로 살아 있다.
2. **테스트도 history 에**: `p4 print -q .../tui/tests/test_mcp_bridge.py#2` 에 12 cases (respx 기반).
3. **import 후보**: `mcp/src/whooing_mcp/official_mcp.py` (archived but functional).

## CL #51003 — 0.7.4 — `mcp_bridge` 자체 HTTP JSON-RPC 클라이언트로 재작성 (archived 의존 제거) (2026-05-10)

archived `whooing-mcp-server-wrapper` 의 `OfficialMcpClient` 에 의존하던
`mcp_bridge.py` 를 자체 HTTP JSON-RPC 클라이언트로 재작성. archived
패키지에 대한 잔재 import 가 사라져 본 모듈은 monorepo `mcp/` 가 사라져도
독립 동작.

### 수정

- `tui/src/whooing_tui/mcp_bridge.py`
  * `from whooing_mcp.official_mcp import OfficialMcpClient` 제거.
  * `httpx.AsyncClient` 로 직접 POST + JSON-RPC envelope (`{jsonrpc:
    "2.0", id, method, params}`).
  * 헤더: `X-API-Key` + `Content-Type: application/json` +
    `Accept: application/json, text/event-stream` (MCP spec).
  * `list_tools()` / `call(name, arguments)` public API 는 동일.
  * `tools/call` 결과의 `isError: True` → `ToolError(UPSTREAM)` 변환,
    `content[].text` 메시지 추출.
  * JSON-RPC error code 매핑: -32700~-32600 (표준 4xx) → USER_INPUT,
    그 외 / 일반 예외 / non-JSON / `result` 누락 → UPSTREAM.
  * `_req_id` 카운터로 호출별 id 증가 (1, 2, 3, ...).
  * 더 이상 `DeprecationWarning` 발사하지 않음 — archived 의존이 없으므로
    deprecation 명분 변경 (기능 자체는 미래 활용 후보).

- `tui/tests/test_mcp_bridge.py` — 완전 재작성, 12 cases:
  * `respx` 로 후잉 공식 MCP 응답을 mock — 실 네트워크 없이 검증.
  * envelope 검증: `tools/list` / `tools/call` 의 method, params,
    `X-API-Key` 헤더.
  * `tools/call` 의 `arguments` 보존, `structuredContent` 반환.
  * `isError: True` → ToolError + content.text 추출.
  * JSON-RPC error -32600 / -32601 → USER_INPUT, -32000 → UPSTREAM.
  * non-JSON 응답 (502 + HTML) → UPSTREAM.
  * `result` 누락 → UPSTREAM.
  * `httpx.ConnectError` (network) → UPSTREAM.
  * `_req_id` 가 1씩 증가.
  * **회귀 방지**: 모듈 source 에 `from whooing_mcp` / `import whooing_mcp`
    가 없는지 단위 테스트로 영구 검증.

### 수정 (문서)

- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.7.3 → 0.7.4.

### 검증

- `make test-tui` → 182 passed (Phase 6 152 + cli 18 + mcp_bridge 12).
- 기존 6 cases 전부 새 12 cases 로 대체. 부수 효과: `DeprecationWarning`
  더 이상 발생 X.

### 의도적 누락

- mcp_bridge 의 UI 통합 (HomeScreen / ReportsScreen) 은 여전히 미구현.
  본 CL 은 라이브러리 layer 의 archived 의존만 제거.
- archived `mcp/` 디렉토리 자체는 그대로 (CL #50999 결정 유지).

## CL #50993 — 0.7.3 — whooing-mcp-server-wrapper 종료 반영 (archive 표기) (2026-05-10)

자매 프로젝트 `whooing-mcp-server-wrapper` 가 종료됐다. 코드는 monorepo
의 `mcp/` 디렉토리에 archive 형태로 보존되며, 본 CL 은 그 사실을 코드
docstring / 문서 / `.env.example` 에 일관 표기하고 `mcp_bridge.py` 에
`DeprecationWarning` 을 추가한다.

### 수정 (영향 받는 14 files)

- `tui/src/whooing_tui/__init__.py` — package docstring 의 wrapper 페어
  설명을 "본래 ~ wrapper 종료 (archived 2026-05-10) 이후 ~" 형태로.
- `tui/src/whooing_tui/auth.py` — module docstring + `_env_candidates()`
  의 공통 위치 설명 + `load_auth_from_env()` 에러 메시지에서 "wrapper 와
  공유" 표현을 "(archived 2026-05-10)" 표기로 정정.
- `tui/src/whooing_tui/cache.py` — `whooing-data.sqlite` 분리 사실의
  타이밍을 archived 명시.
- `tui/src/whooing_tui/client.py` — wrapper 와 같은 규칙으로 동작한다는
  과거 사실에 archived 표기.
- `tui/src/whooing_tui/dates.py` / `errors.py` — 같은 패턴.
- `tui/src/whooing_tui/data.py` — wrapper 가 read-only SELECT 한다는
  가정이 historical 임을 명시. `open_ro()` API 는 미래 새 도구 합류 가능
  성을 위해 유지.
- `tui/src/whooing_tui/mcp_bridge.py` —
  * 모듈 docstring 에 `archived 2026-05-10` 박스 + 신규 호출자 권장
    (REST 직접 또는 자체 MCP 클라이언트).
  * `__init__` 에서 **`DeprecationWarning`** 발사 — 후속 정리에서 제거
    또는 자체 클라이언트로 재작성 예정.
  * ImportError 안내 메시지에 "archived" 명시.
- `tui/pyproject.toml` — comment 의 "외부 consumer (예: wrapper)" 를
  archived 표기로.
- `README.md` (monorepo root) — 디렉터리 표에 `mcp/` 추가 + archived
  표기. "관련 프로젝트" 섹션 갱신.
- `tui/README.md` — 머리말 박스 + Phase 6 까지 진행 상황 (0.7.3) 갱신.
- `tui/DESIGN.md` — §2 자매 도구 mermaid 에 점선 / archived 표기 + §4
  제목.
- `tui/MEMORY.md` — §2 자매 도구 표 갱신, §3 토큰 / 카드 비밀번호 사실
  업데이트, §4 제목, §5.3 변경 전파 규칙을 historical 로.
- `.env.example` (monorepo root) — 공통 위치 안내 박스의 wrapper 공유
  명분을 "단순 path / XDG 표준" 으로 정정.
- `tui/CHANGELOG.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.7.2 → 0.7.3.

### 검증

- `make test-tui` → 176 passed (회귀 없음, `DeprecationWarning` 6건은
  `test_mcp_bridge.py` 의 의도된 부수효과).

### 원칙

- 코드 자체 (mcp/ 디렉토리) 는 보존 — historical 참조 자료 + `mcp_bridge`
  의 동작 유지.
- 문서는 "(archived 2026-05-10)" 일관 표기 — 새 독자가 사실 관계를 즉시
  파악.
- 의도적 코드 중복 (`auth` / `dates` / `errors` / `client` read 부분) 은
  추출 비용이득이 작고 미래 옵션 가치가 있어 그대로 둔다.

## CL #50987 — 0.7.2 — Step 4 MCP 직접 호출 scaffolding (2026-05-10)

후속 권장 1번. 후잉 공식 MCP (https://whooing.com/mcp) 호출 thin bridge.
같은 monorepo 의 `mcp/` 패키지가 이미 `OfficialMcpClient` 로 HTTP JSON-RPC
를 구현해 놨으므로 본 모듈은 그 클라이언트를 wrap 해 TUI 컨벤션
(`ToolError`) 으로 결과를 변환만 한다.

### 추가

- `tui/src/whooing_tui/mcp_bridge.py` — `WhooingMcpBridge` 클래스.
  * 지연 import (`from whooing_mcp.official_mcp import OfficialMcpClient`).
    monorepo 외부 환경에서 import 실패 시 `ToolError("INTERNAL", ...)` 로
    명확한 안내.
  * `list_tools()` / `call(name, arguments)` 위임.
  * `_to_tool_error()` — `OfficialMcpError` 의 JSON-RPC code 가 -32600
    근처면 USER_INPUT, 그 외는 UPSTREAM. 일반 예외 (timeout 등) 도
    UPSTREAM.
- `tui/tests/test_mcp_bridge.py` — 6 cases:
  * monkeypatch 로 `whooing_mcp.official_mcp` 모듈을 fake 로 교체해 실
    네트워크 없이 검증.
  * ImportError → `ToolError(INTERNAL)`.
  * 정상 위임 (list_tools / call_tool 인자 보존).
  * `OfficialMcpError(code=-32000)` → UPSTREAM, `code=-32600` → USER_INPUT.
  * `TimeoutError` 같은 일반 예외 → UPSTREAM.

### 수정

- `tui/DESIGN.md` §6 의 6번 (MCP 직접 호출) — ✅ scaffolding 마킹.
  본격 UI 통합 (보고서 / 예산 / 자주입력 매칭 등) 은 후속 CL.
- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.7.1 → 0.7.2.

### 검증

- `make test-tui` → tui 176 passed (Phase 6 152 + cli 18 + mcp_bridge 6).

### 의도적 누락 (UI 통합)

- HomeScreen / 별도 ReportsScreen 등에서 `WhooingMcpBridge` 호출 노출.
- 본 CL 은 라이브러리 layer 만 — 후속에서 화면 / 키바인딩 추가.

## CL #50980 — 0.7.1 — cli.py 헤드리스 dispatch 단위 테스트 (2026-05-10)

권장 후속 4번. 0.5.1 의 `make coverage` 에서 `cli.py` 가 0% 였던 것을
보충 — 의미있는 수치로 끌어올림.

### 추가

- `tui/tests/test_cli.py` — 18 cases:
  * sections list 의 표 / `--json` 출력 + sanitize_for_log 의 secret
    masking 검증.
  * 종료 코드 매핑: AUTH=3, RATE_LIMIT=4, UPSTREAM=5, USER_INPUT=2.
  * 토큰 누락 (load_auth_from_env ValueError) → exit 2.
  * accounts list — `--section` 명시 / `WHOOING_SECTION_ID` env / 자동
    첫 섹션 선택 분기.
  * entries list — 기본 윈도우 / 명시 `--start --end` / `--start` 만 줄
    때 USER_INPUT / 잘못된 YYYYMMDD / 음수 days / `--json` / 빈 결과
    `(empty)` + `총 0건` 표기.
  * `--help` 가 SystemExit(0) + sections/accounts/entries 모두 노출.
  * 서브커맨드 없을 때 GUI 부팅 진입 (run_app 의 토큰 검증으로 exit 3).

### 수정

- `tui/CHANGELOG.md` — 본 항목.
- `tui/MEMORY.md` — §8 변경이력.
- `tui/pyproject.toml` + `__init__.py` — 0.7.0 → 0.7.1.

### 검증

- `make test` → core 72 + tui 170 = **242 passed** (Phase 6 152 + 18 new cli).
- `cli.py` 라인 커버: **0% → 91%** (150 lines, 13 missed — 주로 verbose
  로깅 / unreachable internal command path).

### 학습된 패턴 (후속 단위 테스트에 재사용)

cli.py 가 `WhooingClient(auth)` 인스턴스화 + `WhooingClient.flatten_accounts(...)`
staticmethod 양쪽을 호출. monkeypatch 가 함수로 교체하면 staticmethod
접근이 깨진다 → **fake `__new__` 클래스 + 진짜 staticmethod 보존**
패턴 (`_FakeWhooingClient`) 으로 둘 다 통과시킴.

## v0.7.0 — Phase 6 (statement import / annotator / attachment / dashboard) (2026-05-10)

monorepo 의 sibling `whooing-core` 라이브러리에서 어댑터 / 첨부 storage /
SQLite 스키마를 받아 다음 화면 4개 추가. `whooing-mcp-server-wrapper` v0.2.0
이 같은 db 를 read-only 로 SELECT — TUI 가 owner.

### Added

* **Phase 6.2** `tui/src/whooing_tui/data.py` — WHOOING_DATA_DIR root +
  `init_shared_schema()` + `open_rw()` / `open_ro()` (whooing_core.db 위 layer).
* **Phase 6.3** `screens/statement_import.py` — Statement Import Wizard
  (HTML/PDF). issuer auto-detect → password modal → adapter → dedup → preview
  → entries-create + statement_import_log.
* **Phase 6.4** `screens/annotator.py` — AnnotatorModal (entry_id 단위 메모 +
  해시태그). `parse_hashtags_input()` helper.
* **Phase 6.5** `screens/attachment_browser.py` — entry 별 첨부 list / add (path
  modal) / delete / open (`open` / `xdg-open`).
* **Phase 6.6** `screens/dashboard.py` — at-a-glance: import 통계 + annotation
  카운트 + 첨부 합계 + db meta.

### Tests

  `tui/tests/test_data.py`                     9 tests
  `tui/tests/test_statement_import.py`         10 tests
  `tui/tests/test_annotator.py`                 9 tests
  `tui/tests/test_attachment_browser.py`       12 tests
  `tui/tests/test_dashboard.py`                10 tests
  → +50 tests; total tui pytest 152 passed.

### Notes

* 모든 신규 화면이 `whooing_tui.data.{open_rw, open_ro}` 사용 — wrapper 와
  같은 db 공유 (WAL + busy_timeout=5000 으로 동시 SELECT 안전).
* CLI / app.py 에 'i' (import) / 'a' (annotator) / 'A' (attachments) / 'D'
  (dashboard) 단축키 wiring 은 follow-up. 현재는 push_screen() 호출 가능한
  programmatic API 만 제공.

## CL #50961 — 0.6.1 — `.env` 공통 위치 (Step 10) (2026-05-10)

후잉 토큰을 양쪽 도구 (whooing-tui + whooing-mcp-server-wrapper) 가
공유하도록 공통 위치 `~/.config/whooing/.env` 채택. backward compatible —
기존 project root `.env` 도 fallback 으로 계속 동작.

### 수정

- `tui/src/whooing_tui/auth.py`
  - `_env_candidates()` 함수 분리. 우선순위: ① `$WHOOING_ENV` (절대
    경로 override) → ② `~/.config/whooing/.env` (공통) → ③ project
    root 의 `.env` (legacy).
  - `load_dotenv(c, override=False)` — 셸 export 된 환경변수가 항상
    최우선.
  - 첫 발견 후보 1개만 로드 (override=False 라 의미상 동등).
  - 미설정 시 에러 메시지에 권장 위치 (`~/.config/whooing/.env`) 안내.
- `.env.example` (monorepo root) — 탐색 우선순위 + 공통 위치 마이그레이션
  방법 (`mkdir / mv / chmod 600`) 안내 박스 추가.
- `tui/tests/test_auth.py` — 3 cases 추가:
  * `_env_candidates()` 후보에 공통 위치 포함.
  * `$WHOOING_ENV` override 가 첫 후보.
  * `WHOOING_ENV=<file>` 가 가리키는 토큰 로드.
- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목.
- `tui/pyproject.toml` + `__init__.py` — 0.6.0 → 0.6.1.

### Cross-project (mcp-server-wrapper 측, 별도 CL)

같은 양쪽 정렬을 위해 `whooing-mcp-server-wrapper` 의 `server.py` 에도
공통 위치 후보 추가 (CL #50962). wrapper 도 `~/.config/whooing/.env` 를
우선 탐색하고 기존 `~/.config/whooing-mcp/.env` 는 backward compat 으로
유지.

### 검증

- `make test` (monorepo) → core 72 + tui 102 = **174 passed**.
- `cd ../whooing-mcp-server && pytest -q` → **188 passed** (wrapper 회귀 없음).

### 사용자 마이그레이션 (선택)

토큰을 한 곳에서 관리하려면:

```bash
mkdir -p ~/.config/whooing
mv .env ~/.config/whooing/.env       # whooing-tui 의 .env 또는
                                     # whooing-mcp-server 의 .env
chmod 600 ~/.config/whooing/.env     # 권장
```

하지 않아도 기존 `.env` 가 그대로 동작 — backward compat 보장.

## CL #50956 — 0.6.0 — 화면 도움말 모달 (Step 7) (2026-05-10)

### 추가

- `tui/src/whooing_tui/screens/help.py` — `HelpModal(ModalScreen[None])`.
  현재 활성 Screen 의 `BINDINGS` 를 introspect 해서 `show=True` 인 항목을
  표 형태로 노출. 같은 description 의 키들 (예: `q` / `ctrl+c`) 은 한 줄
  로 묶어 보인다. `body_text: str` attribute 으로 평문 본문을 보관해 테스트
  가 Static 의 사적 API (`renderable`) 에 의존하지 않게.
- `tui/tests/test_help_modal.py` — 5 cases:
  * `_format_bindings()` 단위: hidden 제외 / 같은 description 묶기 / 빈
    visible 케이스 안내 메시지.
  * 통합: HomeScreen `?` (action_help) → HelpModal push, 본문에
    "Entries" / "Refresh" / "Help" 포함 → dismiss → HomeScreen 복귀.
  * 통합: EntriesScreen → Help → 본문에 "New" / "Delete" / "Edit" /
    "Refresh".

### 수정

- `tui/src/whooing_tui/screens/home.py` — `?` (`question_mark`) 키
  바인딩 추가 (priority, key_display="?"). `action_help()` 가 `HomeScreen`
  타이틀과 자체 BINDINGS 를 HelpModal 로 push.
- `tui/src/whooing_tui/screens/entries.py` — 같은 패턴으로 `?` /
  `action_help()`.
- `tui/CHANGELOG.md` / `tui/MEMORY.md` — 본 항목 + 메모.
- `tui/pyproject.toml` + `__init__.py` — 0.5.1 → 0.6.0.

### 검증

- `make test` → core 72 + tui 99 (= 5 new help cases) = **171 passed**.

### 학습된 함정 (Phase 2c 의 학습과 일관)

- textual 8.x 의 `Static` 은 `renderable` 속성이 없음 (반복 함정). 화면
  자체에 평문 attribute (`HelpModal.body_text`) 를 보관해 테스트가
  내부 API 에 의존하지 않게.
- ModalScreen 의 `escape` binding 으로 textual 이 dispatch 시 `'list'
  object has no attribute 'key_to_bindings'` 같은 internal AttributeError
  를 보일 수 있음 (textual 8.2.5 환경). 테스트는 키 시뮬 대신 `dismiss(None)`
  직접 호출로 단축. 실 사용자 경험에서는 escape 정상 동작.

## CL #50951 — 0.5.1 — README 갱신 + 콘솔 스크립트 smoke + coverage 인프라 (2026-05-10)

작은 인프라 정리. Step 5/6/8 묶음.

### 수정

- `tui/README.md` — Phase 1 시점의 outdated 안내문을 Phase 3 까지의 진행
  반영. monorepo 구조 (`make install` from monorepo root) 명시. **TUI 키
  바인딩 요약 표** 추가 — HomeScreen / EntriesScreen / EntryEditDialog 의
  주요 키.
- `tui/pyproject.toml` — dev deps 에 `pytest-cov>=4` 추가.
- `Makefile` (monorepo root) — `make coverage` (tui 의 pytest --cov,
  HTML report → `htmlcov/`) + `make smoke-cli` (콘솔 스크립트 + `python
  -m` 양쪽 진입점이 모두 동작함을 dispatch 만으로 검증, 토큰 불필요)
  타겟 추가.
- `.gitignore` — `.coverage` / `htmlcov/` / `coverage.xml` 차단 추가.
- `pyproject.toml` + `__init__.py` — 0.5.0 → 0.5.1.

### 검증

- `make smoke-cli` → "OK — 양쪽 진입점 모두 동작."
- `make coverage` → 94 passed, **73% 라인 커버** (1208 lines, 330 missed).
  주요 미커버는 cli.py 0% (서브커맨드 dispatch — 헤드리스 단위 테스트 미작성),
  __main__.py 0%, app.py 62% (run_app 의 GUI 부팅 코드), edit_entry.py
  69% (UI 폼 핸들러). 핵심 라이브러리 (auth/dates/errors/models/state) 는
  100%, client.py 80%, cache.py 81%, entries.py 83%, home.py 92%.
- `make test` → 166 passed (회귀 없음).

### 의도적 누락 (다음 CL 로)

- 화면 도움말 모달 (`?` 키) — Phase 2c 부터 자리만 잡혀 있음.
- `.env` 공통 위치 (whooing-mcp-server-wrapper 와 함께 옮기는
  cross-project 작업).

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
