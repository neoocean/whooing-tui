# 01. 시작하기

## 목적

처음 환경을 셋업하고 TUI 를 띄워 첫 거래 목록을 본다. 토큰 누락 / P4
미설정 등 흔한 진입 장애를 빠르게 분리한다.

## 주요 경로

```
make install
  └─ python -m venv .venv + pip install -e core/[dev] tui/[dev] mcp/[dev]
.env 작성 (또는 ~/.config/whooing/.env)
  └─ WHOOING_AI_TOKEN=<token>
make run | python3 whooing.py | python -m whooing_tui
  └─ cli.main() → run_app()
      ├─ load_auth_from_env() : 토큰 누락 시 stderr + rc 3
      └─ WhooingTuiApp.run()
          ├─ on_mount → _StartupCheckScreen 푸시 (시나리오 09)
          └─ 통과 시 EntriesScreen 푸시
```

## 단계별 흐름

1. **패키지 설치** — `make install` 한 번. `.venv/` 가 만들어지고 core,
   tui, mcp 가 editable 로 설치된다.
2. **토큰 설정** — `cp .env.example .env` 후 `WHOOING_AI_TOKEN` 채움.
   토큰 누락이면 TUI 가 뜨지 않고 stderr 로 안내 + rc 3.
3. **첫 실행** — `make run`. 화면 진행:
   1. "데이터베이스 상태를 확인합니다…" 모달 (시나리오 09).
   2. 통과 시 EntriesScreen — 최근 N 일 (기본 30) 거래.
   3. 첫 진입 시 섹션 picker / accounts list 가 자동 부팅.
4. **첫 거래 확인** — Footer 의 단축키 (`?` 로 풀 리스트). 데이터가
   비어있으면 `+ 새 거래 추가` sentinel row 가 표시되며 Enter 로
   추가 다이얼로그.

## 트러블슈팅

- **`make install` 실패 (BOM / 인코딩)** — pyproject.toml 의 UTF-8 BOM
  이슈는 [MEMORY.md §5.3](../../tui/MEMORY.md) 의 P4 filetype 정책
  (`text+C`) 으로 해결됨. 새 환경에서 같은 증상이면 그 섹션 참조.
- **`p4` 명령 부재** — startup check 가 자동 silent skip. P4 환경
  없이도 앱은 정상 동작 (로컬 sqlite 만 사용).
- **섹션 자동 선택이 잘못된 섹션** — `WHOOING_SECTION_ID` env 또는
  state.json (`~/.config/whooing-tui/state.json`) 의 마지막 활성
  섹션이 우선. 둘 다 없으면 첫 섹션. `s` 키로 재선택.

## 관련 코드

- [`whooing.py`](../../whooing.py) — sys.path 부트스트랩.
- [`cli.py`](../../tui/src/whooing_tui/cli.py) — `main()`, `run_app()`.
- [`app.py`](../../tui/src/whooing_tui/app.py) — `WhooingTuiApp`, on_mount.
- [`auth.py`](../../tui/src/whooing_tui/auth.py) — 토큰 로딩.
- [`screens/entries.py:430~470`](../../tui/src/whooing_tui/screens/entries.py)
  — EntriesScreen 자동 부팅 (sections + accounts + entries chain).
