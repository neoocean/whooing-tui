# whooing-tui

후잉 가계부([whooing.com](https://whooing.com))를 **터미널에서 빠르게**
다루기 위한 Textual TUI. 본 패키지는 monorepo 의 `tui/` 서브디렉토리이며,
같은 monorepo 의 [`core/`](../core) (whooing-core 라이브러리) 와
[`whooing-mcp-server-wrapper`](../../whooing-mcp-server) (LLM 자동화) 가
같은 후잉 REST API 를 공유한다.

**현재 0.5.0 — Phase 3 까지 완료**:

- Phase 1: 핵심 라이브러리 + 헤드리스 CLI
- Phase 2a: HomeScreen (섹션 picker + 활성 섹션 계정과목 트리)
- Phase 2b: EntriesScreen (DataTable + 100-cap 인지 footer)
- Phase 2c: EntryEditDialog + WhooingClient CRUD (POST/PUT/DELETE)
- Phase 3: sqlite 캐시 (accounts 1h TTL / entries 5min TTL,
  mutation 시 자동 invalidate)

자세한 로드맵·아키텍처는 [`DESIGN.md`](./DESIGN.md), 변경 이력은
[`CHANGELOG.md`](./CHANGELOG.md).

---

## 빠른 시작

```bash
# 1. monorepo 루트에서 가상환경 + 의존성 설치 (Python 3.11+)
cd ..      # tui/ 의 부모 (monorepo root)
make install

# 2. 후잉 AI 토큰 설정 (monorepo root 의 .env)
cp .env.example .env
# .env 의 WHOOING_AI_TOKEN 을 실 토큰으로 교체
# 발급: 후잉 → 사용자 > 계정 > 비밀번호 및 보안 > AI 토큰 발급

# 3. 헤드리스 CLI 로 동작 확인
.venv/bin/python -m whooing_tui sections list
.venv/bin/whooing-tui accounts list      # 콘솔 스크립트도 동등하게 동작
.venv/bin/whooing-tui entries list --days 7

# 4. TUI 실행 — HomeScreen → 'e' 로 EntriesScreen → 'n'/Enter/d 로 거래 추가/수정/삭제
make run
```

## TUI 키 바인딩 요약

### HomeScreen

| 키 | 동작 |
| --- | --- |
| ↑/↓ | 섹션 picker 이동 |
| Enter | 선택된 섹션 활성화 (계정과목 로드) |
| `e` | EntriesScreen 진입 |
| `r` | 캐시 invalidate + 재로드 |
| `t` | 테마 토글 |
| `q` / Ctrl+C | 종료 |

### EntriesScreen

| 키 | 동작 |
| --- | --- |
| ↑/↓ | 거래 행 이동 |
| `n` | 새 거래 입력 (EntryEditDialog) |
| Enter | 선택 거래 수정 (EntryEditDialog) |
| `d` | 선택 거래 삭제 (ConfirmModal) |
| `r` | 캐시 invalidate + 재로드 |
| `+` / `-` | 조회 윈도우 ±7일 |
| `q` / Esc | HomeScreen 복귀 |

### EntryEditDialog

| 키 | 동작 |
| --- | --- |
| Tab | 필드 이동 |
| Ctrl+S | 저장 |
| Esc | 취소 |

`left` / `right` 필드는 `account_id` (예: `x20`) 와 표시명 (예: `식비`,
대소문자 무시) 양쪽 입력을 지원합니다.

## 헤드리스 CLI

서브커맨드 없이 실행하면 Textual TUI 가 열리고, 다음 서브커맨드는 GUI 없이
바로 실행된다 (cron / 스크립트 친화).

| 명령 | 설명 |
| --- | --- |
| `whooing-tui sections list` | 섹션(가계부) 목록 |
| `whooing-tui accounts list [--section s133178]` | 활성 섹션의 계정과목 |
| `whooing-tui entries list [--days N \| --start YYYYMMDD --end YYYYMMDD]` | 거래내역 |

공통 옵션:

- `--json` — 결과를 JSON 으로 출력 (기본은 정렬된 표)
- `-v` / `-vv` — INFO / DEBUG 로그
- `--section <ID>` — 섹션 명시. 미지정 시 `WHOOING_SECTION_ID` 또는 첫 섹션

## 설정 파일 (선택)

`tui/whooing-tui.toml.example` 을 `tui/whooing-tui.toml` 로 복사하면
테마·기본 조회 윈도우·캐시 옵션을 조정할 수 있다.

```toml
[ui]
theme = "textual-dark"   # 또는 "textual-light"
entries_page_size = 50

[entries]
default_window_days = 30

[cache]
enabled = true            # 끄면 매 호출이 후잉 REST 로
accounts_ttl_sec = 3600   # 1시간
entries_ttl_sec = 300     # 5분
```

탐색 우선순위는 `$WHOOING_TUI_CONFIG` → `<project>/tui/whooing-tui.toml` →
`~/.config/whooing-tui/config.toml` 순.

## 개발

monorepo 루트의 Makefile 이 양쪽 패키지 (core + tui) 를 일괄 다룬다:

```bash
make install      # venv + core + tui editable install + dev deps
make test         # pytest -q (core + tui 양쪽)
make test-tui     # tui 만
make coverage     # tui 의 pytest --cov (HTML report → htmlcov/)
make run          # python -m whooing_tui (TUI)
make sections     # sections-list 헤드리스 smoke
make clean        # cache 디렉토리 제거
```

테스트는 `respx` 로 후잉 API 를 모킹하므로 토큰 없이도 돌아간다.
실 후잉 호출 검증은 `make sections` (`.env` 의 토큰 필요).

## 라이선스

MIT — `LICENSE` 참고 (monorepo root).
