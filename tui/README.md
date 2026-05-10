# whooing-tui

후잉 가계부([whooing.com](https://whooing.com))를 **터미널에서 빠르게**
다루기 위한 Textual TUI. 본 패키지는 monorepo 의 `tui/` 서브디렉토리이며,
같은 monorepo 의 [`core/`](../core) (whooing-core 라이브러리) 를 import 한다.

> **자매 도구 정리 (2026-05-10)** — 본래 같은 후잉 REST API 를 공유하던
> [`whooing-mcp-server-wrapper`](../mcp) 는 archived. 코드는 monorepo 의
> [`mcp/`](../mcp) 에 보존되며, TUI 의 `mcp_bridge.py` 가 그 `OfficialMcpClient`
> 를 한정적으로 호출 (deprecated, 후속 정리 예정).

**현재 0.15.0 — Phase 6 + ... + db 를 project/db/ 로 + P4 자동 submit (CL #51107)**:

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

# 3. 헤드리스 CLI 로 동작 확인 — 진입점 3종 모두 동등
.venv/bin/python -m whooing_tui sections list      # 패키지 module
.venv/bin/whooing-tui accounts list                # 콘솔 스크립트
.venv/bin/python ../whooing.py entries list --days 7   # monorepo 루트 진입점

# 4. TUI 실행 — 진입 즉시 거래내역이 표시 (자체 부팅: sections + accounts + entries)
#    s = 섹션 변경 / a = 계정과목 / n / Enter / d = 거래 추가/수정/삭제
make run
# 또는: .venv/bin/python ../whooing.py
```

## TUI 키 바인딩 요약

### EntriesScreen (초기 화면, 0.11.1+)

평소엔 거래 목록만 보이고, 거래 목록 맨 위 row 에서 **↑ 한 번 더** 누르면
**`[+ 새 거래 추가]` sentinel** 등장 (CL #51074+). 거기서 Enter 가 새 거래
dialog. sentinel 에서 ↓ 누르면 숨김 + cursor 가 첫 실거래로 복귀. 빈
entries 일 때는 sentinel 자동 표시 (진입점 보장).

거래 화면은 두 가지 상태를 가집니다 (CL #51064+):

1. **파란 row cursor 만** — 첫 mount 상태. 거래 단위 선택.
2. **파란 + 노란 column marker** — ←/→ 누르면 활성화. cell 단위 선택.

| 키 | 파란만 (초기) | 파란+노랑 (컬럼 활성) |
| --- | --- | --- |
| ↑/↓ | 거래 행 이동 | 거래 행 이동 (marker 가 따라감) |
| ←/→ | **컬럼 marker 활성화** | 활성 컬럼 ±1 (boundary clamp) |
| Enter | 거래 수정 (EntryEditDialog) | 컬럼별 컨텍스트 (아래 표) |
| Esc | **noop** — 종료 안 함 | 컬럼 marker 해제 + **활성 필터 동시 해제** |
| `q` | 앱 종료 | 앱 종료 |
| `e` | 거래 수정 | 거래 수정 |
| `n` | 새 거래 입력 | 새 거래 입력 |
| `d` | 선택 거래 삭제 (ConfirmModal) | 〃 |
| `c` | 활성 필터 해제 | 〃 |
| `s` | 섹션 picker 모달 | 〃 |
| `a` | 계정과목 화면 | 〃 |
| `r` | 캐시 invalidate + 재로드 (필터/marker 자동 해제) | 〃 |
| `+` / `-` | 조회 윈도우 ±7일 | 〃 |
| `?` | 화면 도움말 | 〃 |

**컬럼 활성 상태에서 Enter** — 컬럼별 컨텍스트 액션:

| 활성 컬럼 | Enter 동작 |
| --- | --- |
| `date` | 같은 날짜의 거래만 필터 (sub-index 무시 — `20260510` 매칭) |
| `left` | 같은 차변 항목만 필터 (`l_account_id` 비교) |
| `right` | 같은 대변 항목만 필터 (`r_account_id` 비교) |
| `item` | 괄호 바깥 키워드 매칭 (예: `스타벅스(커피)` → `스타벅스`. `외식(저녁, 불고기)` → `외식`) |
| `money` / `memo` | 거래 수정 dialog (EntryEditDialog) |

필터가 활성일 때는 status bar 가 노란색 (warn) 으로 `필터: left=x20 — 4/12건. c 로 해제 / r 로 재로드.` 와 같이 안내합니다. **종료는 q 만** (Esc 는 컬럼 marker / 활성 필터 해제 전용 — 사용자 의도치 않은 종료 방지).

**`c` vs `Esc` 차이** (0.10.3+):

| 키 | 컬럼 marker | 활성 필터 |
|---|---|---|
| `c` (Clear) | 그대로 유지 | **해제** |
| `Esc` (Cancel) | **해제** | **해제** (있으면) |

`c` 는 같은 컬럼에서 다른 row 를 선택해 재필터하고 싶을 때 (marker 보존), `Esc` 는 필터링 작업 자체를 끝낼 때.

### SectionPickerScreen (`s` 로 push)

| 키 | 동작 |
| --- | --- |
| ↑/↓ | 섹션 이동 |
| Enter | 선택 → 활성 섹션 변경 + EntriesScreen 자동 재로드 |
| `r` | 섹션 목록 재로드 |
| `q` / Esc | 취소 |

### AccountsScreen (`a` 로 push)

| 키 | 동작 |
| --- | --- |
| ↑/↓ | 계정과목 이동 |
| `n` | 새 계정과목 추가 (AccountEditDialog) |
| Enter | 선택된 계정과목 수정 |
| `d` | 삭제 (사전 검사 + 사용자 확인) |
| `r` | 캐시 invalidate + 재로드 |
| `?` | 화면 도움말 |
| `q` / Esc | EntriesScreen 으로 복귀 |

### EntryEditDialog (0.12.0+)

| 키 | 동작 |
| --- | --- |
| Tab | 필드 이동 |
| Ctrl+S | 저장 |
| Esc | 취소 |
| Enter (left/right 위) | 계정과목 picker 모달 |

**필드별 입력 규칙** (CL #51076+):

- **date** — `YYYY-MM-DD` 형식. 숫자만 입력해도 자동으로 `-` 가 삽입됨
  (예: `20260509` → `2026-05-09`). 사용자가 직접 `-` 를 타이핑해도 무시됨.
- **money** — 천단위 콤마 자동 포매팅 (`1,234,567`). 입력 중 실시간 갱신.
- **left / right** — *이름* 으로 표시 (`식비  (x20)`). Enter / 클릭 →
  `AccountPickerScreen` 모달 (Tree widget). **카테고리 헤더** (자산/부채/
  자본/수입/지출/그룹) + **항목 leaf** 2-level 구조. 0.13.0+ 부터는 사용자가
  카테고리를 먼저 펼쳐 보고 그 안의 항목을 선택. 현재 선택된 항목의
  카테고리는 자동 펼침 + cursor 이동.
- **item** — 적요 (예: `스타벅스`). 후잉의 item 필드와 동일.
- **memo** — 후잉의 memo + 로컬 sqlite 의 `entry_annotations.note` 에
  동시 저장 (검색·통계용 미러).
- **tags** — 해시태그 (로컬 sqlite only, 후잉에는 보내지 않음). 직접
  타이핑 또는 **Enter → `TagsPickerScreen` 모달** (0.13.0+):
    - **추천**: item / memo 본문에 매칭되는 기존 태그 (예: item=`외식 점심`,
      memo=`식비 결제` → `식비`, `외식` 가 추천 섹션 상단).
    - **자주 쓰는 태그**: 사용 빈도 내림차순.
    - 타이핑 시 prefix/substring 필터, `+ 새 태그 만들기: <input>` 옵션
      으로 새 태그 즉시 생성.
  공백 / `,` / `#` 모두 분리자, 중복 제거.

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
