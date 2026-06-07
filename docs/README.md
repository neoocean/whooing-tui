# whooing-tui 문서

본 디렉토리는 **사용자/유지보수자/LLM 협업자** 를 위한 시나리오 중심 문서.
설계 노트는 [`../tui/DESIGN.md`](../tui/DESIGN.md), 운영 메모/팀원 인계는
[`../tui/MEMORY.md`](../tui/MEMORY.md), 변경 이력은
[`../tui/CHANGELOG.md`](../tui/CHANGELOG.md) 를 참조.

본 디렉토리의 문서는 **"무엇을 하기 위한 경로"** — 사용자가 키보드로
무엇을 누르고 화면이 어떻게 흘러가는지, 코드의 어느 파일이 어떤 책임을
지는지 한눈에. LLM 협업자도 코드 검색 전에 본 시나리오부터 참조하면
컨텍스트 비용이 절반 이하로.

## 시나리오 카탈로그

| # | 시나리오 | 핵심 키 | 주요 파일 |
|---|---|---|---|
| 01 | [시작하기](scenarios/01-getting-started.md) | `python3 whooing.py` | `cli.py`, `app.py` |
| 02 | [거래 추가 / 수정 / 삭제](scenarios/02-add-edit-delete-entry.md) | `n` / `e` / `d` | `screens/entries.py`, `screens/edit_entry.py` |
| 03 | [영수증·인보이스 첨부](scenarios/03-attach-files.md) | `f` / 메뉴 | `screens/attachment_browser.py`, `screens/receipt_attach.py` |
| 04 | [카드 명세서 가져오기](scenarios/04-import-card-statement.md) | 메뉴 → 카드 import | `screens/statement_import.py` |
| 05 | [중복 거래 평가 + 정리 (선택 / 일괄 스캔)](scenarios/05-evaluate-duplicates.md) | `space` + `m` (선택) / 입력 메뉴 (일괄) | `screens/dupe_eval.py`, `screens/dupe_scan_overview.py`, `screens/duplicate_scan.py`, `core/dupes.py`, `dupe_scan_repo.py` |
| 06 | [해시태그 + 일괄 태그](scenarios/06-hashtags-and-batch-tagging.md) | `#` / `m` 메뉴 | `screens/tags_picker.py`, `screens/tag_management.py` |
| 07 | [필터 + 검색](scenarios/07-filter-and-search.md) | `←/→` + `Enter` / `/` | `filters.py`, `screens/entries.py` |
| 08 | [보고서 + 통계](scenarios/08-reports.md) | `t` | `screens/reports.py`, `official_mcp.py` |
| 09 | [시작·종료 시 P4 동기화](scenarios/09-startup-shutdown.md) | (자동) / `q` | `app.py` (`_StartupCheckScreen`/`_ShutdownModal`), `p4_sync.py` |
| 10 | [저장소: SQLite vs plaintext 검토](scenarios/10-storage-sqlite-vs-plaintext.md) | (설계 문서) | `core/db.py` |
| 11 | [거래 수정 이력·소프트 삭제·복원](scenarios/11-edit-history-and-soft-delete.md) | `e`/`d`/`H` + 휴지통 | `revision_repo.py`, `core/revisions.py`, `screens/trash.py`, `screens/revision_history.py` |

## 부속 문서

- [`MAINTAINABILITY-REVIEW.md`](MAINTAINABILITY-REVIEW.md) — 코드 감사
  결과 + LLM 친화도 향상 제안 (실행 비순서 백로그).

## 새 시나리오를 추가할 때

- 파일명은 `NN-짧은-영문-슬러그.md` (NN 은 두자리 일련번호).
- 본 README 의 카탈로그 표에 한 줄 추가.
- 각 시나리오 문서는 다음 4 섹션을 가진다:
  1. **목적** — 한두 줄로 "이 시나리오로 무엇을 하나".
  2. **주요 경로** — 키 / 메뉴 / 화면 전이.
  3. **단계별 흐름** — 사용자 관점의 step-by-step.
  4. **관련 코드** — `file:line` 단위로 LLM 이 즉시 jump 할 수 있게.

## 메타

코드와 본 문서가 어긋나면 *코드가 진실이고 본 문서가 따라온다.* 본
문서는 "왜 이 흐름인가" 를 보존한다 — 미세한 행위 차이는 직접 코드를
읽는 게 정확.
