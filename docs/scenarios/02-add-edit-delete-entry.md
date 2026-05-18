# 02. 거래 추가 · 수정 · 삭제

## 목적

가계부의 가장 기본적인 행위 — 거래 한 건의 CRUD. 후잉 REST API
(`entries-create/update/delete`) 와 로컬 sqlite annotation 동기화 를
함께 본다.

## 주요 경로

| 키 | 액션 | 결과 |
|---|---|---|
| `n` / Enter (sentinel row) | `action_new_entry` | EntryEditDialog push (빈 폼) |
| `e` | `action_edit_entry` | EntryEditDialog push (현 cursor row prefill) |
| `d` | `action_delete_entry` | ConfirmModal → yes → 삭제 |
| `m` | `action_show_context_menu` | 수정/삭제/첨부/새거래 메뉴 |

## 단계별 흐름

### 추가 (`n`)

1. EntriesScreen 의 `n` 키 또는 sentinel row 에서 Enter → `EntryEditDialog`.
2. 폼: date / money / left / right / item / memo / tags.
3. account 필드는 picker 모달 — section 의 모든 계정과목을 트리 형태로.
4. 사용자가 OK → `EntryDraft` dataclass 로 dismiss → `_submit_create` 워커
   가 `client.create_entry()` 호출.
5. 성공 시 응답에서 `entry_id` 회수 → 로컬 sqlite 에 memo + 해시태그
   upsert → `refresh_entries()` 재로드.

### 수정 (`e`)

1. cursor 가 거래 row 에 있으면 `_selected_entry()` 가 dict 반환.
2. 로컬 sqlite 에서 해시태그 prefill (`_fetch_local_tags`).
3. 다이얼로그 dismiss 시 `_submit_update` 워커가 `client.update_entry()`
   호출 + 로컬 sqlite 도 동일 키로 upsert.

### 삭제 (`d`)

1. 거래 요약 (date / money / left / right / item) 을 ConfirmModal 로 보임.
2. 사용자가 yes → `_submit_delete` 워커가 `client.delete_entry()`.
3. 성공 시 `_purge_local()` 이 sqlite annotation/해시태그/첨부 row 도
   정리 (orphan 방지) → `refresh_entries()`.

## 로컬 sqlite 미러링

후잉 API 가 entry 단위 free-form memo / 해시태그를 지원하지 않으므로
TUI 가 로컬 sqlite (`<project>/db/whooing-data.sqlite`) 에 별도 보관.
- 스키마: `entry_annotations(entry_id, section_id, note, …)` +
  `entry_hashtags(entry_id, tag)`. 자세한 컬럼은 [`core/db.py`](../../core/src/whooing_core/db.py).
- mutation 마다 `submit_db_to_p4()` 가 fire-and-forget 으로 P4 자동 submit
  — 다른 환경에서 다음 시작 시 sync 받음 (시나리오 09).

## 에지 케이스

- **entry_id 가 비어있는 거래** — UI 가 status 로 "수정 / 삭제 불가"
  안내. 후잉 측에서 새 entry 생성 직후 row 가 잠시 partial 일 수
  있어 한 박자 기다린 후 재시도.
- **계정 type 조회 실패** — 사용자가 새로 만든 계정이 캐시 미반영 시.
  `accounts-list` 를 다시 받으세요 status. 메뉴 → 계정과목 (`a`) 로 갱신.
- **mutation 도중 P4 자동 submit 실패** — silent skip. UI 영향 없음.
  종료 시 `flush_on_exit` 가 누락분 마지막 안전망 처리 (시나리오 09).

## 관련 코드

- [`screens/entries.py:2017~2255`](../../tui/src/whooing_tui/screens/entries.py)
  — `action_new_entry` / `action_edit_entry` / `action_delete_entry` +
  worker 들 (`_submit_create`, `_submit_update`, `_submit_delete`).
- [`screens/edit_entry.py`](../../tui/src/whooing_tui/screens/edit_entry.py)
  — `EntryEditDialog`, `EntryDraft`, `ConfirmModal`.
- [`screens/account_picker.py`](../../tui/src/whooing_tui/screens/account_picker.py)
  — 계정 트리 picker.
- [`client.py`](../../tui/src/whooing_tui/client.py) — `create_entry`,
  `update_entry`, `delete_entry`.
- [`core/db.py`](../../core/src/whooing_core/db.py) — sqlite 스키마.
