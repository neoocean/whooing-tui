# whooing-tui (monorepo)

후잉 가계부([whooing.com](https://whooing.com)) 의 사용자 도구를 모아 둔 monorepo.
세 개의 독립 설치 가능 Python 패키지로 구성됩니다:

| 디렉터리 | 패키지 | 버전 | 역할 |
|---|---|---|---|
| [`core/`](core/) | `whooing-core` | 0.1.0 | 어댑터 / SQLite 스키마 (v8) / 첨부 storage / 미리보기 / **entries 캐시** — 라이브러리. TUI 가 import. |
| [`tui/`](tui/) | `whooing-tui` | **0.63.0** | Textual 기반 터미널 UI. statement import wizard / entry annotator / attachment browser / dashboard / 보고서 (공식 후잉 MCP 위임) / Ctrl/Shift multi-select / iPhone Blink 한글 자모 조합. 사용자 가시 layer. |
| [`mcp/`](mcp/) | `whooing-mcp-server-wrapper` | 0.2.1 (archived) | **archived 2026-05-10**. LLM 자동화용 MCP 서버 — historical 참조용. 본 monorepo 의 다른 코드는 더 이상 import 안 함 (CL #51008 의 `mcp_bridge.py` 제거 + CL #52755 의 자체 `official_mcp.py` 도입). |

## 목적

후잉의 외부 입력 (카드사 명세서) 을 정형화 + 첨부 + 메모/태그를 SQLite 에
보관하는 user-facing 도구. **whooing-tui** 가 db + attachments owner —
사용자가 직접 입력/편집. wrapper 는 archived 라 더 이상 db 를 SELECT 하는
외부 consumer 는 없으나, `open_ro()` API 와 read-only 분리 정책은
미래 도구의 합류 가능성을 위해 유지.

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
├── mcp/                      ← archived 2026-05-10 (whooing-mcp-server-wrapper)
│   ├── pyproject.toml        MCP 서버 + 14 도구 (parsers/sms, tools/audit 등)
│   ├── src/whooing_mcp/
│   └── tests/
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
make install     # 모든 패키지 editable install
make test        # core / tui / mcp 모두 pytest
make test-core   # core 만
make test-tui    # tui 만 (가장 자주)
make test-mcp    # mcp 만 (archived — 회귀 검증 용도)
make coverage    # tui 의 라인 커버리지
make smoke-cli   # whooing-tui 콘솔 진입점 검증
make run         # TUI
make clean
```

## 관련 프로젝트

- [`mcp/`](mcp/) — **archived 2026-05-10**. wrapper 패키지의 *코드* 는
  monorepo 안에 보존되어 historical 참조 + 회귀 검증용. 신규 import 는
  하지 않는다.
- 단, *공식 후잉 MCP 서버* (`https://whooing.com/mcp`) 는 TUI 의
  보고서·예산·목표 위임 경로에서 **현역으로 사용 중** — `tui/src/
  whooing_tui/official_mcp.py` 가 JSON-RPC 클라이언트. 이는 위 wrapper
  와는 별개의 코드 + 별개의 server.

`docs/` 디렉토리:
- [`docs/README.md`](docs/README.md) — 시나리오 카탈로그.
- [`docs/scenarios/`](docs/scenarios/) — 사용자 워크플로 단위 가이드 9개.
- [`docs/MAINTAINABILITY-REVIEW.md`](docs/MAINTAINABILITY-REVIEW.md) — 유지
  보수 백로그.
- [`CLAUDE.md`](CLAUDE.md) — AI 어시스턴트용 진입점 / 모듈 맵.

## License

MIT — `LICENSE` 파일 참고.
