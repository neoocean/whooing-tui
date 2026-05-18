# Whooing TUI — orientation for AI assistants

본 monorepo 에서 작업하는 LLM (Claude / Codex 등) 을 위한 진입점 / 모듈
맵 / 작업 패턴 요약. 코드를 검색하기 *전에* 본 파일부터 읽으면 컨텍스트
비용이 크게 절약된다.

## 진입점 한눈에

```
python3 whooing.py              # sys.path 부트스트랩 (tui/src, core/src)
make run                         # 동등 (Makefile 단축)
.venv/bin/python -m whooing_tui  # 동등 (패키지 module)
  └─ whooing_tui/cli.py: main() → run_app()
      └─ whooing_tui/app.py: WhooingTuiApp.run()
```

설치 / 환경:
- `make install` — `.venv/` 생성 + `core/`, `tui/` editable + playwright
  chromium 자동. (CL #52846: archived `mcp/` 는 monorepo 에서 제거됨.)
- `.env` 에 `WHOOING_AI_TOKEN` 필요. `~/.config/whooing/.env` 도 OK.
- 시작 / 종료 동작 정책은 [`docs/scenarios/09-startup-shutdown.md`](docs/scenarios/09-startup-shutdown.md).

## 패키지 / 디렉토리 맵

```
whooing-tui/                     # 본 monorepo 루트
├── whooing.py                   # 부트스트랩 스크립트
├── Makefile                     # 단축 명령어
├── core/                        # 라이브러리 (pure, Textual 의존 없음)
│   └── src/whooing_core/
│       ├── db.py                # sqlite v8 스키마 + 마이그레이션
│       ├── attachments.py       # 파일 첨부 storage (sha256 dedup)
│       ├── entries_cache.py     # entries 캐시 (점진 확장 지원)
│       ├── preview.py           # 텍스트/PDF 미리보기
│       ├── hangul.py            # 한글 자모 조합 (iOS Blink 대응)
│       ├── dupes.py             # 중복 거래 평가 휴리스틱 (pure)
│       ├── csv_adapters/        # 카드사 명세서 CSV 어댑터
│       ├── html_adapters/       # HTML 명세서 어댑터
│       ├── pdf_adapters/        # PDF 명세서 어댑터
│       └── receipt/             # 영수증 PDF 메타 추출
├── tui/                         # Textual UI — core 에 의존
│   └── src/whooing_tui/
│       ├── app.py               # WhooingTuiApp + _StartupCheckScreen + _ShutdownModal
│       ├── cli.py               # main() / run_app()
│       ├── client.py            # WhooingClient + CachedWhooingClient
│       ├── auth.py              # 토큰 load + mask
│       ├── data.py              # db_path() / attachments_root() / init_shared_schema()
│       ├── p4_sync.py           # P4 자동 submit + 시작 시 동기화 검사
│       ├── filters.py           # 클라이언트-사이드 컬럼 필터
│       ├── ime.py               # 한글 두벌식 ↔ 영문 매핑 + bind_ko()
│       ├── cache.py             # accounts/entries inter-session 캐시
│       ├── config.py            # whooing-tui.toml 로딩
│       ├── models.py            # Pydantic + 도메인 dataclass
│       ├── constants.py         # 모든 매직 상수 (CL #52834+)
│       ├── official_mcp.py      # 공식 후잉 MCP 위임 (보고서)
│       ├── state.py             # SessionState (활성 섹션 + accounts 인덱스)
│       ├── text_utils.py        # 한글 / 약어 / 회사명 처리 (CL #52834+)
│       ├── widgets/
│       │   ├── menubar.py       # F10 메뉴 + MenuBarMixin
│       │   ├── confirm_modal.py # yes/no 확인 모달 (CL #52834+ 이동)
│       │   └── hangul_input.py  # Input 의 한글 자모 조합
│       └── screens/             # 각 모달 / 본 화면 (큰 책임 단위)
└── docs/
    ├── README.md                # 시나리오 카탈로그
    ├── scenarios/               # 사용자 워크플로 가이드 9개
    └── MAINTAINABILITY-REVIEW.md  # 백로그 (적용 진행 중)
```

CL #52846 (0.71.0) 부터 archived `mcp/` 패키지 제거됨 — 코드베이스에 두
패키지 (core / tui) 만 남음. mcp wrapper 가 필요하면 P4 history #52845
이전으로 sync.

## 핵심 클래스 / 화면

| 클래스 | 위치 | 역할 |
|---|---|---|
| `WhooingTuiApp` | `app.py` | 앱 entry. on_mount → 시작 check → EntriesScreen. |
| `EntriesScreen` | `screens/entries.py` | 초기 화면. 가장 큰 모듈 (~3000 줄). 새 기능 대부분 여기로. |
| `EntryEditDialog` | `screens/edit_entry.py` | 거래 add/edit 폼. `EntryDraft` 로 dismiss. |
| `WhooingClient` | `client.py` | REST 클라이언트. CachedWhooingClient 가 래핑. |
| `_StartupCheckScreen` | `app.py` | 시작 시 P4 sync 확인 모달. |
| `_ShutdownModal` | `app.py` | 종료 시 진행 작업 표시 + 취소 불가 모달. |
| `DuplicateEvalScreen` | `screens/dupe_eval.py` | 중복 평가 + dedup UI. |
| `ReportsScreen` | `screens/reports.py` | 좌/우 패널 보고서 모달. |

## 주요 디자인 패턴

1. **Textual `@work(exclusive=True, group=...)`** — race 방지. 같은
   group 은 새 호출이 이전을 자동 cancel. 예: `_fetch_worker` (보고서),
   `_evaluate_duplicates_worker`.
2. **bind_ko(en, action, ...)** — 영문 + 한글 자모 양쪽 binding 한 번에.
   IME 가 켜진 사용자도 같은 키로 작동.
3. **로컬 sqlite mirror** — 후잉 API 가 모르는 메모/태그/첨부는 sqlite
   (`<project>/db/whooing-data.sqlite`) 에 별도 보관. 매 mutation 마다
   fire-and-forget `submit_db_to_p4` → 다른 환경 sync.
4. **공식 후잉 MCP 위임** — 보고서 / 통계는 직접 REST 가 fragile 해
   `official_mcp.py` 가 JSON-RPC 로 위임 (`https://whooing.com/mcp`).
5. **set_status(msg, error=True|False)** — 화면 하단 status 영역. 모든
   action 의 결과 보고. CL #52834+ 에서 `@safe_action` 데코레이터로 일부
   action 의 try/except → set_status 보일러플레이트 제거.

## 작업할 때 자주 보는 문서

| 무엇을 하려는가 | 어디를 보나 |
|---|---|
| 새 키 추가 | `tui/DESIGN.md`, `screens/entries.py` BINDINGS |
| 새 보고서 | `docs/scenarios/08-reports.md`, `screens/reports.py` `_build_menu` |
| 새 명세서 어댑터 | `docs/scenarios/04-import-card-statement.md`, `core/csv_adapters/` |
| 새 화면 | `docs/scenarios/`, `screens/edit_entry.py` 패턴 |
| P4 동기화 정책 | `docs/scenarios/09-startup-shutdown.md`, `p4_sync.py` |
| 한글 / IME | `docs/scenarios/02-…` 의 한글 입력 부분, `core/hangul.py` |
| 모달 추가 | 기존 ModalScreen 따라가기 (`dupe_eval.py`, `tags_picker.py`) |

## 테스트

- `make test` — core + tui 패키지 모두. 단위 + 통합 (~980 통과).
- `make test-core` / `make test-tui` — 패키지별.
- 통합 테스트는 `pytest-asyncio` + Textual `App.run_test()` pattern.
- 새 화면을 만들면 `tui/tests/` 에 통합 테스트도 한 쌍 — fake client +
  `_open_entries` 같은 helper.

## 흔한 함정

1. **`HelpModal._bindings` 같은 Textual 내부 attribute 충돌** — Screen
   에서 `self._bindings = ...` 같이 set 하면 BindingsMap 을 덮어써
   다음 키 입력에서 AttributeError. CL #52816 의 회귀 참조.
2. **Modal 안에서 `q` priority** — 모달 BINDINGS 의 priority=True 가
   App / Screen 의 q 보다 먼저. 종료 모달은 q noop, 일반 모달은 닫기.
3. **`@work(exclusive=True)` 의 race** — 같은 worker 가 빠르게 두 번
   호출되면 첫 작업이 취소. cleanup 코드를 `finally` 에 둬도 안전하지
   않다 (cancel 은 await 지점에서). `_current_*` 같은 id 비교로 보호.
4. **WHOOING_DATA_DIR env** — 테스트 격리 plugin. 모든 startup / shutdown
   helper 가 이 env set 이면 P4 호출 skip. 통합 테스트가 통과하는 이유.

## 더 깊이 보고 싶다면

- 사용자 워크플로 단위: [`docs/scenarios/`](docs/scenarios/).
- 설계 의도: [`tui/DESIGN.md`](tui/DESIGN.md), [`core/DESIGN.md`](core/DESIGN.md).
- 인계 / 운영 메모: [`tui/MEMORY.md`](tui/MEMORY.md) (GitHub 미러에는 안 올라감).
- 변경 이력: [`tui/CHANGELOG.md`](tui/CHANGELOG.md).
- 유지보수 백로그: [`docs/MAINTAINABILITY-REVIEW.md`](docs/MAINTAINABILITY-REVIEW.md).
