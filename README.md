# whooing-tui (monorepo)

후잉 가계부([whooing.com](https://whooing.com)) 의 사용자 도구를 모아 둔 monorepo.
두 개의 독립 설치 가능 Python 패키지로 구성됩니다:

| 디렉터리 | 패키지 | 역할 |
|---|---|---|
| [`core/`](core/) | `whooing-core` | 어댑터 / SQLite 스키마 / 첨부 storage — 라이브러리. 다른 consumer (TUI, MCP wrapper) 가 import. |
| [`tui/`](tui/) | `whooing-tui` | Textual 기반 터미널 UI. statement import wizard / entry annotator / attachment browser. 사용자 가시 layer. |

## 목적

후잉의 외부 입력 (카드사 명세서) 을 정형화 + 첨부 + 메모/태그를 SQLite 에
보관하는 user-facing 도구. 같은 머신의
[whooing-mcp-server-wrapper](../whooing-mcp-server) (LLM 자동화) 와 데이터
(SQLite + attachments) 를 공유:

- **whooing-tui** — db + attachments **owner**. 사용자가 직접 입력/편집.
- **whooing-mcp-server-wrapper** — 같은 db read-only. LLM 응답 augmentation
  (`local_annotations`, `local_attachments`).

## 빠른 시작

```bash
# 1. 두 패키지 모두 설치 (단일 venv)
make install

# 2. .env 설정 (한 .env 가 두 패키지 모두에서 읽힘)
cp .env.example .env
# WHOOING_AI_TOKEN, (선택) WHOOING_CARD_HTML_PASSWORD 설정

# 3. TUI 실행
make run
```

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
├── README.md                 이 파일
├── Makefile                  install / test 두 패키지 동시 처리
├── LICENSE                   MIT
├── .env.example
└── .gitignore
```

## 개발

```bash
make install     # core + tui 모두 editable install
make test        # core/ 와 tui/ 양쪽 pytest
make test-core   # core/ 만
make test-tui    # tui/ 만
make run         # tui 실행
make clean       # __pycache__ 등 제거
```

## 관련 프로젝트

- [whooing-mcp-server-wrapper](../whooing-mcp-server) — LLM 자동화용 MCP 도구.
  본 monorepo 의 `whooing-core` 를 dependency 로 import. db read-only.

## License

MIT.
