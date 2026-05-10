# whooing-tui

후잉 가계부([whooing.com](https://whooing.com))를 **터미널에서 빠르게**
다루기 위한 Textual TUI. 같은 워크스페이스의 [`whooing-mcp-server-wrapper`](../whooing-mcp-server)
와 핵심 라이브러리(REST 클라이언트·인증·날짜·에러 매핑)를 공유한다.

> **Phase 1 (현재)** — 프로젝트 골격 + 헤드리스 CLI 가 동작.
> Phase 2 에서 Textual 화면(섹션 picker / 거래내역 표 / 입력 dialog) 이
> 들어간다. 자세한 로드맵은 [`DESIGN.md`](./DESIGN.md) 참고.

---

## 빠른 시작

```bash
# 1. 가상환경 + 의존성 설치 (Python 3.11+)
make install

# 2. 후잉 AI 토큰 설정
cp .env.example .env
# .env 의 WHOOING_AI_TOKEN 을 실 토큰으로 교체
# 발급: 후잉 → 사용자 > 계정 > 비밀번호 및 보안 > AI 토큰 발급

# 3. 헤드리스 CLI 로 동작 확인
.venv/bin/python -m whooing_tui sections list
.venv/bin/python -m whooing_tui accounts list
.venv/bin/python -m whooing_tui entries list --days 7

# 4. TUI 실행 (Phase 1 은 자리표시자 화면)
make run
```

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

`whooing-tui.toml.example` 을 `whooing-tui.toml` 로 복사하면 테마와 기본
조회 윈도우 등 UI 옵션을 조정할 수 있다.

```toml
[ui]
theme = "textual-dark"   # 또는 "textual-light"
entries_page_size = 50

[entries]
default_window_days = 30
```

탐색 우선순위는 `$WHOOING_TUI_CONFIG` → `<project>/whooing-tui.toml` →
`~/.config/whooing-tui/config.toml` 순.

## 개발

```bash
make install   # venv + editable install + dev deps
make test      # pytest -q
make run       # TUI 실행
make clean     # cache 디렉터리 제거
```

테스트는 `respx` 로 후잉 API 를 모킹하므로 토큰 없이도 돌아간다.

## 라이선스

MIT — `LICENSE` 참고.
