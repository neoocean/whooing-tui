# 06. 해시태그 + 일괄 태그

## 목적

거래 한 건 또는 여러 건에 `#카테고리` 형태의 해시태그를 붙여 후속 필터
/ 집계를 쉽게. 후잉 API 가 태그 개념을 모르므로 *순수 로컬* — sqlite
`entry_hashtags` 에 저장.

## 주요 경로

```
EntriesScreen
  ├─ 단일 거래 + e (또는 새 거래 폼) → EntryEditDialog 안에서 # 입력
  │
  ├─ # 키 → action_batch_tag
  │   └─ 선택 1건 이상이면 그 set 에 적용,
  │      0건이면 cursor 거래 하나에만 적용 (편의).
  │       └─ TagsPickerScreen → 단일 태그 선택 → add_tag_to_entries
  │
  └─ 메뉴 → 해시태그 관리…  → TagManagementScreen
      ├─ 섹션의 모든 태그 list + 사용 횟수.
      ├─ 태그 rename (사용처 모두 일괄 변경).
      └─ 태그 삭제.

EntriesScreen 표시
  └─ item 컬럼 안에 인라인 `#태그1 #태그2 ...` (제한 = config.tags.inline_limit)
```

## 단계별 흐름

### 단일 거래 태그 추가

1. EntryEditDialog 의 tags 필드. # 없이 입력 → 저장 시 자동 prefix 안 함
   (raw 그대로 sqlite 저장).
2. 다이얼로그 dismiss → `_submit_create` / `_submit_update` 워커가 후잉
   API + 로컬 sqlite 양쪽에 반영.

### 일괄 태그 (`#`)

1. EntriesScreen 에서 `space` 로 거래들 선택.
2. `#` (또는 한글 자판 영문 동일) → `TagsPickerScreen`.
3. 한 태그 선택 → `add_tag_to_entries(eids, tag)` 가 sqlite 에 추가
   (이미 있던 거래는 skip — `(entry_id, tag)` UNIQUE).
4. 결과 status: `#카페 → 12건 추가 (이미 있던 3건 skip). selection 해제`.
5. 자동 P4 submit 으로 다음 환경에 전파.

### 해시태그 관리 (메뉴)

1. 메뉴 → 화면 → 해시태그 관리…
2. 현 섹션의 태그 list — 사용 횟수 내림차순.
3. rename: 사용처 모두 일괄 update. 삭제: 모든 entry 에서 제거.

## 인라인 표시 정책

- `_render_item_cell_with_tag_marker` (entries.py) — item 컬럼 안에서
  태그 표시. 컴팩트 레벨에 따라 잘라 보여줌.
- `_item_tag_inline_limit()` 의 limit 가 0 이면 모든 태그 표시
  (CL #52777+ 사용자 요청).
- 화살표 → 와 `Enter` 로 *태그 단위 cursor* 도 가능 — 태그 선택 후
  `Enter` 면 그 태그로 즉시 필터 (시나리오 07 참조).

## 에지 케이스

- **태그에 공백 / 한글** — 모두 허용. 후잉 memo 와 별도로 sqlite 에 저장.
- **cross-section 오염 방지** — 모든 query 는 `section_id` 필터.
  같은 entry_id 가 두 섹션에 존재할 수 없는 후잉 정책 + 본 TUI 의
  방어적 필터.
- **삭제 거래의 태그** — `_purge_local()` 가 함께 정리.

## 관련 코드

- [`screens/tags_picker.py`](../../tui/src/whooing_tui/screens/tags_picker.py)
  — 태그 선택 모달.
- [`screens/tag_management.py`](../../tui/src/whooing_tui/screens/tag_management.py)
  — 태그 일괄 관리 화면.
- [`screens/entries.py:875~919`](../../tui/src/whooing_tui/screens/entries.py)
  — `action_batch_tag`, `_batch_tag_worker`.
- [`core/db.py`](../../core/src/whooing_core/db.py) —
  `add_tag_to_entries`, `set_hashtags`, `get_annotations_for`.
