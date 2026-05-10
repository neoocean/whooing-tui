# whooing-tui — 변경 이력

각 항목은 Perforce CL 단위로 끊는다.

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
