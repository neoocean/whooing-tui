# whooing-tui (monorepo)

후잉 가계부([whooing.com](https://whooing.com)) 의 사용자 도구를 모아 둔 monorepo.
두 개의 독립 설치 가능 Python 패키지로 구성됩니다:

| 디렉터리 | 패키지 | 역할 |
|---|---|---|
| [`core/`](core/) | `whooing-core` | 어댑터 / SQLite 스키마 (v8) / 첨부 storage / 미리보기 / entries 캐시 / 중복 평가 — 라이브러리. TUI 가 import. |
| [`tui/`](tui/) | `whooing-tui` | Textual 기반 터미널 UI. statement import wizard / entry annotator / attachment browser / dashboard / 보고서 (공식 후잉 MCP 위임) / Ctrl/Shift multi-select / iPhone Blink 한글 자모 조합. 사용자 가시 layer. |

> **mcp/ 패키지는 CL #52846 (0.71.0) 에서 제거.** archived 2026-05-10 이후
> 본 코드베이스에서 한 번도 import 되지 않아 보존 가치가 사라짐. P4
> history 의 #52845 이전으로 sync 하면 복구 가능. *공식 후잉 MCP 서버*
> (`https://whooing.com/mcp`) 위임은 그대로 — `tui/src/whooing_tui/
> official_mcp.py` 가 직접 JSON-RPC 호출.

## 목적

후잉의 외부 입력 (카드사 명세서) 을 정형화 + 첨부 + 메모/태그를 SQLite 에
보관하는 user-facing 도구. **whooing-tui** 가 db + attachments owner —
사용자가 직접 입력/편집.

## 빠른 시작

```bash
# 1. 패키지 모두 설치 (단일 venv)
make install

# 2. .env 설정 (한 .env 가 모든 패키지에서 읽힘)
cp .env.example .env
# WHOOING_AI_TOKEN 설정. 권장 위치는 ~/.config/whooing/.env

# 3. TUI 실행 — 셋 다 동등
make run                              # Makefile 단축 (가장 짧음)
.venv/bin/python whooing.py           # 본 디렉터리의 진입점 스크립트
.venv/bin/python -m whooing_tui       # 패키지 module
```

`whooing.py` 는 monorepo 루트에 두고 `tui/src` / `core/src` 를 sys.path
에 prepend 한 뒤 `whooing_tui.cli.main()` 으로 위임 — 셋 다 같은 코드 경로
를 거쳐 동작이 100% 동일.

자세한 사용법:
- TUI 키보드 단축키 / CLI 흐름: [`tui/README.md`](tui/README.md)
- 라이브러리 API: [`core/README.md`](core/README.md)

## 디렉터리 레이아웃

```
whooing-tui/                  ← 본 monorepo (이 README)
├── core/                     ← whooing-core 라이브러리
│   ├── pyproject.toml
│   ├── src/whooing_core/
│   │   ├── html_adapters/    하나/현대카드 보안메일 .html 파서
│   │   ├── csv_adapters/     신한/국민/현대/삼성카드 CSV 파서
│   │   ├── pdf_adapters/     신한/현대카드 PDF 명세서 파서
│   │   ├── attachments.py    sha256 dedup 파일 storage
│   │   ├── db.py             SQLite 스키마 + open_rw / open_ro
│   │   └── dates.py          KST helper
│   └── tests/
├── tui/                      ← whooing-tui 패키지
│   ├── pyproject.toml        whooing-core 를 in-tree 의존성으로 명시
│   ├── src/whooing_tui/
│   └── tests/
├── docs/                     ← 시나리오 가이드 + 유지보수 백로그
├── whooing.py                ← `python whooing.py` 진입점 (sys.path 셋업
│                                후 whooing_tui.cli.main 호출)
├── README.md                 이 파일
├── Makefile                  install / test 모든 패키지 처리
├── LICENSE                   MIT
├── .env.example
└── .gitignore
```

## 개발

```bash
make install     # core + tui editable install + playwright chromium
make test        # core + tui pytest
make test-core   # core 만
make test-tui    # tui 만 (가장 자주)
make coverage    # tui 의 라인 커버리지
make smoke-cli   # whooing-tui 콘솔 진입점 검증
make run         # TUI
make clean
```

## 관련 프로젝트

*공식 후잉 MCP 서버* (`https://whooing.com/mcp`) 는 TUI 의 보고서·예산·
목표 위임 경로에서 사용 중 — `tui/src/whooing_tui/official_mcp.py` 가
JSON-RPC 클라이언트. (CL #52846 에서 archived `mcp/` 패키지 제거 후에도
해당 위임은 그대로.)

`docs/` 디렉토리:
- [`docs/README.md`](docs/README.md) — 시나리오 카탈로그.
- [`docs/scenarios/`](docs/scenarios/) — 사용자 워크플로 단위 가이드 9개.
- [`docs/MAINTAINABILITY-REVIEW.md`](docs/MAINTAINABILITY-REVIEW.md) — 유지
  보수 백로그.
- [`CLAUDE.md`](CLAUDE.md) — AI 어시스턴트용 진입점 / 모듈 맵.

## License

MIT — `LICENSE` 파일 참고.
