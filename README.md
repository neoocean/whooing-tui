# whooing-tui

후잉 가계부([whooing.com](https://whooing.com))를 **터미널에서** 빠르게
다루는 [Textual](https://textual.textualize.io/) TUI. 거래 입력·수정·삭제,
카드 명세서 가져오기, 영수증 첨부, 해시태그, 보고서, **수정 이력·휴지통
복원**까지 키보드(와 마우스)로.

![whooing-tui — 거래 목록 화면](docs/image/01-entries.svg)

> 📖 **사용법은 매뉴얼에 있습니다.** 설치부터 일상 사용까지 **실제 화면
> 스크린샷**과 함께 안내하는 **[사용 매뉴얼 → docs/MANUAL.md](docs/MANUAL.md)**
> 를 보세요. 이 README 는 프로젝트 소개입니다.

## 무엇을 하나

- ⌨️ **키보드 중심, 마우스도 지원** — 표에서 바로 이동·입력·필터, `F10`
  메뉴와 컨텍스트 메뉴(`m`)로 거의 모든 동작.
- 💳 **카드 명세서 가져오기** — 하나/현대 보안메일 HTML·여러 카드사 CSV·PDF
  명세서를 파싱해 거래로.
- 📎 **영수증·인보이스 첨부** — sha256 중복제거 로컬 저장.
- 🏷️ **메모·해시태그** — 후잉이 모르는 부가정보를 로컬 SQLite 에.
- ♻️ **수정 이력·소프트 삭제·복원** — 모든 수정/삭제를 되돌릴 수 있게 버전
  이력 보관, 삭제는 휴지통으로([설계](docs/scenarios/11-edit-history-and-soft-delete.md)).
- 📊 **보고서·통계** — 공식 후잉 MCP 위임.
- 🔁 **여러 기계 동기화** — Perforce(또는 클라우드 폴더)로 db·첨부 동기.
  Perforce 없이도 단일 기계에서 완전 동작([방법](docs/scenarios/12-no-perforce-and-multi-machine-sync.md)).
- 🇰🇷 **한글 친화** — IME(두벌식) 켜진 상태에서도 단축키 동작, iOS Blink
  자모 조합 대응.

## 빠른 시작

```bash
make install                 # .venv + core/tui editable + playwright chromium
cp .env.example .env         # WHOOING_AI_TOKEN 설정 (또는 ~/.config/whooing/.env)
make run                     # 실행 (= python3 whooing.py)
```

자세한 설치·사용은 **[docs/MANUAL.md](docs/MANUAL.md)** 참고.

## 구성 (monorepo)

두 개의 독립 설치 가능 Python 패키지:

| 디렉터리 | 패키지 | 역할 |
|---|---|---|
| [`core/`](core/) | `whooing-core` | 라이브러리 — 명세서 어댑터 / SQLite 스키마 / 첨부 storage / entries 캐시 / 중복·수정이력. Textual 의존 없음. |
| [`tui/`](tui/) | `whooing-tui` | Textual 터미널 UI. core 에 의존하는 사용자 가시 layer. |

```
whooing-tui/
├── core/        whooing-core 라이브러리 (어댑터·db·attachments·revisions)
├── tui/         whooing-tui Textual UI (screens·client·p4_sync)
├── docs/        매뉴얼 + 시나리오 가이드 + 스크린샷(docs/image)
├── whooing.py   진입점 (sys.path 셋업 후 whooing_tui.cli.main)
└── Makefile     install / test / run / gen-images
```

## 개발

```bash
make test        # core + tui pytest
make test-tui    # tui 만 (가장 자주)
make coverage    # 라인 커버리지
make gen-images  # docs 스크린샷(SVG) 재생성
```

## 문서

- 📖 [`docs/MANUAL.md`](docs/MANUAL.md) — **사용자 매뉴얼** (스크린샷 포함).
- [`docs/README.md`](docs/README.md) — 시나리오 카탈로그(워크플로별 가이드).
- [`tui/README.md`](tui/README.md) · [`core/README.md`](core/README.md) — 패키지별 개발 노트.
- [`CLAUDE.md`](CLAUDE.md) — AI 어시스턴트용 진입점 / 모듈 맵.

## 관련

- *공식 후잉 MCP 서버* (`https://whooing.com/mcp`) — 보고서·예산·목표 위임
  (`tui/src/whooing_tui/official_mcp.py`).

## License

MIT — [`LICENSE`](LICENSE) 참고.
