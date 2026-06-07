# 11. 거래 수정 이력 · 소프트 삭제 · 복원 (설계 문서)

> **상태: 구현됨 — v0.81.0 (2026-06-07).** 사용자 결정으로 **안 B**(후잉
> 실삭제 + 복원 시 재생성, 동일성은 불변 `logical_id` 로 추적)를 채택하고
> **즉시 기본 동작**으로 적용(설정 플래그 없음). 단계 구현 CL: 57522(코어
> 스키마 v10 + revisions) → 57524(기록 배선) → 57526(휴지통·이력 UI).
> 본 문서는 그 구현의 설계 근거이며 아래 시나리오는 **구현된 안 B** 기준.
> 기본 CRUD 동작은 [02. 거래 추가·수정·삭제](02-add-edit-delete-entry.md) 참조.

## 목적

거래 한 건의 **수정·삭제를 되돌릴 수 있게** 만든다.

- 거래를 수정하면 **수정 전 상태를 보관**한다 (버전 이력).
- 후잉 `entry_id` 는 **항상 최신 상태**를 가리키되, 그 entry 의 **이전
  버전들**을 로컬에 남겨 **수정 내역 + 수정 시점**을 추적한다.
- 삭제는 **실제 데이터 삭제가 아니라 삭제 마킹**만 한다 → **삭제 취소**
  가능.
- 한 entry 의 **전체 변경 이력(삭제 포함)을 조회**하고 **임의 시점으로
  되돌리기(revert)** 가능.
- 되돌린 결과 역시 **같은 entry 의 새로운 최신 버전**으로 관리된다.

## 요구사항 (사용자 스펙 → 체크리스트)

1. 수정 시 직전 상태 보존. (편집 전/후가 모두 이력에 남는다.)
2. 후잉 `entry_id` 는 최신을 가리킨다. 이전 버전은 그 id 에 묶여 추적된다.
3. 각 버전의 **수정 시점(timestamp)** 을 안다.
4. 삭제 = 소프트 삭제(마킹). 실제 행 삭제 금지.
5. 삭제 취소(복원) 가능.
6. entry 전체 변경 이력(삭제 이벤트 포함) 조회.
7. 임의 버전으로 되돌리기. 되돌림도 같은 entry 의 최신 버전이 된다.

## 현재 동작 (as-is) 와 한계

| 동작 | 현재 | 한계 |
|---|---|---|
| 수정 | `e` → `EntryEditDialog` → `client.update_entry()` 가 후잉 entry 를 **제자리 갱신**. 로컬은 `EntryRepository.persist()` 가 memo/태그 mirror. | **수정 전 값이 사라진다.** 이력 없음. |
| 삭제 | `d` → ConfirmModal → `client.delete_entry()` 로 **후잉에서 실삭제** + `EntryRepository.purge()` 가 로컬 annotation/첨부/캐시 정리. | **되돌릴 수 없다.** 첨부 파일은 `.trash/` 로 가나 거래 자체는 복구 불가. |

핵심 문제: 후잉 REST API 는 **버전 이력을 제공하지 않는다**. 따라서
이력/소프트삭제는 **로컬 sqlite 가 system of record** 가 되어야 한다
(후잉은 "현재 살아있는 장부"의 projection). 로컬 sqlite 는 매 mutation
마다 P4 자동 submit 되므로 이력도 다른 환경으로 동기화된다
([09. 시작·종료 P4 동기화](09-startup-shutdown.md) 참조).

## 핵심 개념

### 논리 엔트리(logical entry) 와 anchor

- **`logical_id`**: 한 거래의 **불변 anchor**. 수정·삭제·복원을 가로질러
  바뀌지 않는다. 기본값은 **그 거래의 최초 후잉 `entry_id`** — 따라서
  "수정만 반복하는 흔한 경우"엔 `logical_id == 현재 후잉 entry_id` 로
  사용자가 말한 "동일 entry_id 유지"가 그대로 성립한다.
- **`whooing_entry_id`**: 그 버전 시점에 후잉에 실재하는 entry id. 수정은
  제자리 갱신이라 변하지 않는다. (소프트삭제 정책에 따른 변동은 아래
  "설계 결정" 참조.)

### 버전(revision) 체인

한 `logical_id` 아래 **append-only** 버전 행을 쌓는다. 각 버전 = 그 시점의
거래 전체 스냅샷 + 연산 종류 + 시각. **최신(head) = `revision_no` 최대 행.**

## 데이터 모델 (제안: schema v10)

```sql
-- 거래의 모든 버전 (append-only). 절대 UPDATE/DELETE 하지 않는다.
CREATE TABLE entry_revisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    logical_id       TEXT    NOT NULL,   -- 불변 anchor (최초 entry_id)
    revision_no      INTEGER NOT NULL,   -- logical_id 안 1..N 증가
    whooing_entry_id TEXT,               -- 이 버전 시점의 후잉 id (삭제 상태면 직전 id 보존, NULL 가능)
    section_id       TEXT,
    op               TEXT    NOT NULL,   -- create|edit|delete|restore|revert|external
    -- 거래 본문 스냅샷 (후잉 entries 필드 그대로) ----------------
    entry_date       TEXT,
    l_account        TEXT, l_account_id TEXT,
    r_account        TEXT, r_account_id TEXT,
    money            INTEGER,
    item             TEXT,
    memo             TEXT,
    -- 상태/메타 -------------------------------------------------
    is_deleted       INTEGER NOT NULL DEFAULT 0,  -- 이 버전이 '삭제 마킹' 상태인가
    created_at       TEXT    NOT NULL,   -- 수정/삭제 시점 ISO8601 KST
    source           TEXT,               -- tui|mcp|import|external
    reverted_from    INTEGER,            -- revert 시 원본 revision_no (감사용)
    note             TEXT,               -- 변경 사유(옵션)
    UNIQUE(logical_id, revision_no)
);
CREATE INDEX idx_rev_logical   ON entry_revisions(logical_id, revision_no DESC);
CREATE INDEX idx_rev_whooing   ON entry_revisions(whooing_entry_id);

-- 빠른 '현재 상태' 조회용 head 인덱스 (entry_revisions 에서 파생, 캐시).
CREATE TABLE entry_head (
    logical_id          TEXT PRIMARY KEY,
    section_id          TEXT,
    current_entry_id    TEXT,            -- 현재 후잉 id (삭제 상태면 마지막 id)
    head_revision_no    INTEGER NOT NULL,
    is_deleted          INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);
CREATE INDEX idx_head_current ON entry_head(current_entry_id);
CREATE INDEX idx_head_deleted ON entry_head(is_deleted);
```

- `entry_revisions` 는 **불변 로그**. 모든 분석/되돌리기는 여기서.
- `entry_head` 는 파생 캐시 — 목록 화면이 `entries_cache` 와 join 해
  **삭제 마킹된 거래를 숨기고**, `logical_id ↔ 현재 entry_id` 를 O(1) 로
  해석. (entry_revisions 만으로도 도출 가능하나 매 렌더 max() 회피용.)
- 기존 [`entries_cache`](10-storage-sqlite-vs-plaintext.md) 는 그대로 후잉
  현재 상태 mirror. 이력 테이블은 그 위의 별도 레이어.

## ⚠️ 핵심 설계 결정 — 소프트삭제와 entry_id

후잉이 버전을 모르므로, "삭제 마킹"을 후잉에 **어떻게 반영하느냐**가
유일한 갈림길이다. 두 안을 제시하고 **A 를 권장**한다.

### 권장 — 안 A: TUI 측 소프트삭제 (후잉 실삭제 안 함)

- 삭제 시 후잉 `delete_entry` 를 **호출하지 않는다**. `entry_revisions`
  에 `op=delete, is_deleted=1` 버전을 추가하고 `entry_head.is_deleted=1`.
- TUI 의 모든 목록/보고서/합계는 `is_deleted=1` 을 **숨긴다/제외한다**.
- **장점**: 사용자 스펙("실제 데이터 삭제가 아니라 삭제 마킹만")과 **문구
  그대로 일치**. `whooing_entry_id` 가 보존되어 **복원·되돌리기 시 같은
  entry_id 유지**가 자명. 데이터 손실 위험 0.
- **단점**: 후잉 웹/공식앱·다른 클라이언트에는 그 거래가 **여전히 보인다**
  (TUI 에서만 숨김). 즉 "삭제"는 TUI 의 view 개념.

### 대안 — 안 B: 후잉 실삭제 + 복원 시 재생성

- 삭제 시 후잉에서 실제 삭제(현재 동작) 하되 스냅샷을 이력에 남긴다.
  `whooing_entry_id=NULL`, `is_deleted=1`.
- 복원/되돌리기 시 후잉에 **재생성** → **새 후잉 entry_id** 발급. 이력은
  같은 `logical_id` 로 이어지므로 "논리적으로 같은 거래"는 유지되나,
  **후잉 entry_id 는 바뀐다**.
- **장점**: 삭제가 모든 클라이언트에 반영(장부 합계도 깨끗).
- **단점**: 사용자 요구 "동일 entry_id 의 최신 버전"이 **물리 id 기준으론
  깨진다** (논리 id 로만 동일). 재생성 실패 시 정합성 처리 복잡.

> **채택: 안 B (2026-06-07 사용자 결정).** 리뷰에서 안 A 의 허점 — 후잉에
> 남은 "삭제" 거래가 잔액/보고서(official MCP·accounts-list 의 서버 집계)에
> 계속 합산되어 "삭제했는데 합계엔 남는" 모순 — 이 드러나, 회계적 정확성을
> 위해 안 B 를 택했다. 동일성은 불변 `logical_id` 로 잇고, 복원/되돌리기는
> 같은 logical 의 새 최신 버전(새 후잉 entry_id)이 된다. 즉시 기본 동작으로
> 적용(플래그 없음).

이하 시나리오는 **구현된 안 B** 기준.

## 동작 시나리오 (단계별 흐름)

### 수정 (`e`)

1. `e` → `EntryEditDialog` (현재 head 값으로 prefill).
2. OK → `EntryDraft` dismiss → 수정 워커:
   a. **수정 전 head 스냅샷 확보** (이미 `entry_revisions` 에 있거나, 첫
      수정이면 현재 `entries_cache` 값으로 baseline `op=create` 1버전 seed).
   b. `client.update_entry()` 로 후잉 제자리 갱신 (`entry_id` 불변).
   c. `entry_revisions` 에 `op=edit` 새 버전 append (변경 후 값, `created_at=now`).
   d. `entry_head` 갱신, `entries_cache` 갱신.
   e. `submit_db_to_p4` (이력 포함 동기화).
3. 결과: 같은 entry_id, head=새 버전, 직전 버전 보존.

### 삭제 (`d`, 소프트 — 안 B)

1. `d` → ConfirmModal("삭제할까요? 휴지통에서 복원 가능").
2. yes → 삭제 워커(`_submit_delete`):
   a. `client.delete_entry()` 로 **후잉에서 실제 삭제**(잔액/보고서 정확) —
      기존 동작 유지.
   b. `entry_revisions` 에 `op=delete, is_deleted=1, whooing_entry_id=NULL`
      버전 append (직전 값 스냅샷 — 무엇을 지웠는지 보존), `entry_head.
      is_deleted=1`, `entries_cache` 에서 제거.
   c. 목록에서 사라짐. `submit_db_to_p4`.
3. 첨부/태그/annotation 은 **purge 하지 않는다** (복원 대비 보존). 복원 시
   `migrate_local` 이 새 entry_id 로 재키잉.

### 삭제 취소 / 복원 (휴지통)

1. 화면 메뉴 → **휴지통…** → 삭제 마킹된 거래 목록(`is_deleted=1`).
2. 항목 선택 → **복원**(`_restore_logical`) →
   a. `client.create_entry()` 로 **후잉에 재생성 → 새 entry_id** 발급(안 B).
   b. `entry_revisions` 에 `op=restore, is_deleted=0, whooing_entry_id=새 id`
      버전 append, `entry_head` 갱신(`current_entry_id=새 id`), `entries_cache`
      반영. `migrate_local` 로 메모/태그/첨부를 옛 id→새 id 재키잉.
   c. 목록에 다시 나타남. `logical_id` 는 그대로라 이력이 이어짐. `submit_db_to_p4`.

### 변경 이력 보기 + 되돌리기 (revert)

1. 거래 위에서 **수정 이력…** (예: `H` 키 또는 `m` 메뉴) → `RevisionHistoryScreen`.
2. 버전 목록: `rev# · op · 시각 · 요약(diff)`. 예:
   ```
   v4  edit    2026-06-07 21:53   money 30,000→27,000
   v3  delete  2026-06-06 10:02   (삭제 마킹)
   v2  edit    2026-06-05 19:40   item "저녁"→"저녁(닭칼국수)"
   v1  create  2026-06-04 12:10   최초 입력
   ```
3. 임의 버전 선택 → **이 버전으로 되돌리기** →
   a. 선택 버전의 본문으로 `op=revert` **새 head** append
      (`reverted_from=선택 rev#`).
   b. 현재 **살아있으면** `client.update_entry()` 제자리 갱신(entry_id 불변)
      → `op=revert`; **삭제 상태였으면** 재생성(새 entry_id) → `op=restore`
      + `migrate_local` (복원과 동일 절차, 안 B).
   c. `entry_head`/`entries_cache` 갱신, P4 submit.
4. 결과: 되돌림도 같은 entry 의 **새 최신 버전** — 이력은 끊기지 않고
   계속 누적(되돌리기 자체가 한 줄로 남아 추적 가능).

## UI / UX

| 키 / 진입 | 화면 | 비고 |
|---|---|---|
| `e` | `EntryEditDialog` | 기존. 저장 시 이력 1버전 추가. |
| `d` | ConfirmModal → 소프트삭제 | 문구를 "삭제 마킹/휴지통 복원 가능"으로. |
| `H` 또는 `m`→"수정 이력…" | `RevisionHistoryScreen` (신규 ModalScreen) | 버전 목록 + diff + 되돌리기. |
| 화면 메뉴 → "휴지통…" | `TrashScreen` (신규) | `is_deleted=1` 목록 + 복원/영구삭제. |

- **영구삭제(purge)**: 휴지통에서만, 별도 확인 + (안 B 경우) 후잉 실삭제.
  영구삭제는 이력까지 제거하는 유일한 파괴적 동작 — 신중 모달.
- diff 요약은 인접 버전 필드 비교(`core/` 의 pure 함수로 추출, 테스트 용이).

## 후잉 / P4 동기화 상호작용

- **로컬이 이력의 진실원본**. 후잉은 현재 상태만. `entries_cache` 증분
  갱신([entries_cache](10-storage-sqlite-vs-plaintext.md))과 충돌 없음 —
  이력은 별도 테이블.
- 매 mutation 후 `submit_db_to_p4` 로 `entry_revisions`/`entry_head` 가 다른
  환경에 전파 → **어느 기기에서나 같은 이력/휴지통**.
- **외부 변경 감지**: 후잉 웹/공식앱/MCP 로 TUI 밖에서 거래가 바뀌면 이력에
  안 남는다. TUI 로드 시 `entries_cache`(후잉 현재값) 과 `entry_head`(로컬
  최신값) 가 다르면 `op=external` baseline 버전을 1건 기록해 **간극을
  봉합**(이후 정상 추적). 완전한 외부 추적은 불가 — 한계로 명시.

## 마이그레이션 (schema v9 → v10)

- `entry_revisions`, `entry_head` 테이블 추가 (`core/db.py · init_schema`
  의 마이그레이션 체인). 기존 데이터 무손상.
- **백필 안 함**: 기존 거래는 이력 없이 시작. 각 거래는 **첫 수정/삭제
  시점에** 현재 `entries_cache` 값으로 `op=create` baseline 1버전을 lazy
  seed → 그때부터 추적. (전체 17k+ 거래를 일괄 seed 하지 않아 비용 0.)

## 엣지 케이스 / 동시성

- **다중 환경 동시 수정(P4)**: 서로 다른 기기가 같은 `logical_id` 에 버전을
  추가 → submit 시 sqlite 바이너리 충돌은 P4 가 last-writer 로 처리(현재와
  동일). `revision_no` 충돌 가능 → 머지 시 `created_at` 기준 재번호 +
  양쪽 버전 보존(둘 다 이력에 남김) 규칙 필요. **미해결 — 구현 시 정의.**
- **외부 실삭제**(후잉에서 직접 삭제): `entries_cache` 에서 사라짐 →
  `entry_head` 가 orphan. `op=external`+`is_deleted=1` 로 흡수.
- **첨부/태그**: 소프트삭제는 첨부를 보존(복원 대비). 영구삭제만 `purge()`.
- **되돌리기의 되돌리기**: 단순히 또 한 줄 `op=revert` — 항상 선형 head.

## 단계적 구현 제안 (참고)

1. **v10 스키마 + `EntryRevisionRepository`** (pure 코어 + tui 어댑터, 테스트).
2. 수정 경로에 이력 기록 연결(`e`).
3. 소프트삭제로 `d` 전환 + 목록 필터(`is_deleted` 숨김).
4. `TrashScreen` (복원).
5. `RevisionHistoryScreen` (이력 + 되돌리기 + diff).
6. 외부 변경 감지(`op=external`) + 다중환경 머지 규칙.

각 단계는 독립 CL + 통합 테스트([tests](../../tui/tests/)) 한 쌍.

## 관련 파일

| 책임 | 파일 |
|---|---|
| 후잉 update/delete REST | `tui/src/whooing_tui/client.py` (`update_entry`/`delete_entry`) |
| 로컬 mutation + P4 submit | `tui/src/whooing_tui/repository.py` (`EntryRepository`), 신규 `EntryRevisionRepository` |
| 스키마/마이그레이션 | `core/src/whooing_core/db.py` (`init_schema`), 신규 `core/src/whooing_core/revisions.py` (pure) |
| 현재 상태 캐시 | `core/src/whooing_core/entries_cache.py` |
| 편집 폼 | `tui/src/whooing_tui/screens/edit_entry.py` |
| 목록/삭제/이력 진입 | `tui/src/whooing_tui/screens/entries.py` |
| 신규 화면 | `screens/revision_history.py`, `screens/trash.py` |
| P4 동기화 | `tui/src/whooing_tui/p4_sync.py` |

## 결정 완료 / 미해결

**결정 완료 / 구현됨**
1. 소프트삭제 정책 → **안 B** (즉시 기본 동작, 2026-06-07).
2. 이력 화면 진입 키 → **`H`** + context 메뉴 '수정 이력'; 휴지통 → **화면 메뉴**.
3. **외부 변경 감지(`op=external`)** → v0.82.0 구현. EntriesScreen 진입 첫
   fetch 때 1회, 추적 중인 거래가 **불러온 윈도우 안에 존재하면서 값이 달라진**
   경우 현재 후잉 값으로 external 버전 흡수(`core.revisions.reconcile_external`).

**미해결 (향후)**
1. 다중 환경 `revision_no` 충돌 머지 규칙 — 현재는 P4 last-writer.
2. **외부 삭제** 자동 감지 — 윈도우 한정 fetch 라 부재를 삭제로 단정 불가(오탐
   방지) → 미감지. 전체 ledger 스캔/대상 재조회가 필요해 범위 밖.
3. 이력 보존 기한 / 영구삭제 자동 정리 정책(있을지).
