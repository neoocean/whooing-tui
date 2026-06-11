# 코드 감사 — LLM 친화성 / 보안 / 성능 (0.82.2, 2026-06)

본 문서는 `whooing-tui` 0.82.2 전체를 **LLM 친화성·보안·성능** 세 관점
으로 1회 감사한 결과다. 기존 [`MAINTAINABILITY-REVIEW.md`](MAINTAINABILITY-REVIEW.md)
(CL #52834 백로그, 유지보수성 중심) 와 보완 관계 — 본 문서는 그 이후
누적/회귀된 문제와 보안·성능 신규 항목에 집중한다.

각 항목은 `파일:라인` 근거 + 영향 + 처방. 모든 근거는 현재 코드에서
직접 확인했다(grep/read). 후속 적용 시 본 문서를 살아있는 백로그로 유지.

## 요약

| 영역 | 최우선 문제 | 한 줄 처방 |
|---|---|---|
| LLM | `@safe_action`·`responses.py` 가 **0 호출** 인데 문서엔 "적용됨" | 실제 적용하거나 "미적용 scaffold" 로 문구 강등 |
| LLM | `CLAUDE.md` 모듈 맵 drift (`confirm_modal.py` 오타 + 미문서 파일 5+) | 맵 재생성 |
| 보안 | 카드명세서 HTML 을 네트워크 개방된 headless Chromium 에서 렌더 | context offline + 외부 요청 차단 |
| 보안 | P4 changelist spec 에 첨부 파일명/태그가 무검증 삽입 | spec 직전 제어문자 scrub |
| 성능 | `find_duplicate_clusters` 가 **이벤트 루프에서 동기 실행** → UI freeze | `asyncio.to_thread` 로 offload |
| 성능 | 선택 toggle 마다 DataTable **전체 재렌더** | `update_cell_at` 로 해당 행만 |

---

## 1. LLM 친화성

기존 검토(CL #52834) 이후 코드는 1100→1340(client), 3000→3517(entries)
줄로 **오히려 성장**했고, 그때 "적용" 으로 표기된 두 추상화가 실제로는
연결되지 않았다. 문서가 현실과 어긋나면 LLM 은 존재하지 않는 관례를
모방하려다 턴을 낭비한다 — 크기보다 이쪽이 더 큰 위험이다.

### 1-A. [P1] 문서가 "적용됨" 이라 하는 추상화가 0 호출 (회귀/오도)

- **`@safe_action` 프로덕션 사용 = 0.** `actions.py:52` 정의 + 테스트
  스텁만 존재. `grep -rn "@safe_action" tui/src/` (정의 파일 제외) → **무결과**.
  반면 `screens/entries.py` 에는 `except` 가 **82개**, 데코레이터가 없애려던
  `try/except ToolError→set_status / except Exception→log+set_status`
  보일러플레이트가 그대로 — 예: `screens/entries.py:3053-3061` 한 메서드에서 3회 반복.
  그런데 `MAINTAINABILITY-REVIEW.md:14` 와 `CLAUDE.md` 디자인패턴 §5 는
  "`@safe_action` 데코레이터로 …보일러플레이트 제거" 라고 단언한다.
- **`responses.py` TypedDict import = 0.** `EntryDict`/`SectionDict`/
  `AccountDict`/`CreateEntryResponse` 정의됨. `grep` 프로덕션 import → **무결과**.
  `client.py` 는 이들을 전혀 참조하지 않고 모든 반환이 `dict[str, Any]`/`Any`.
- **처방:** 둘 중 하나. (a) 실제로 적용 — `@safe_action` 을 entries/accounts
  의 `action_*` 에 부착, `responses.py` 타입을 `create_entry`/`list_entries`
  반환에 부착. (b) 적용이 아직이면 두 문서의 "적용/제거" 문구를 "제공되나
  현재 미사용 (opt-in scaffold)" 로 강등. **문구 수정만으로도 즉시 큰 효과.**

### 1-B. [P1] `CLAUDE.md` 모듈 맵이 현실과 어긋남 (신규)

LLM 이 맵을 신뢰해 잘못된 경로를 찾는다.

- **파일명 오타:** `CLAUDE.md:62` 는 `widgets/confirm_modal.py` 라 하나
  실제 파일은 `widgets/confirm.py` (`class ConfirmModal`). 리뷰 doc 은
  맞게(`widgets/confirm`) 적었는데 CLAUDE.md 만 틀림.
- **맵에서 누락된 최상위 파일:** `actions.py`, `dates.py`, `errors.py`,
  `responses.py`, `revision_repo.py` 가 실재하나 디렉토리 맵에 없음.
  특히 `revision_repo.EntryRevisionRepository` 는 trash/revert 기능의
  중추로 entries.py 에서 12회+ 사용되는데 미문서.
- **`widgets/input_modal.py`** (`InputModal`/`TextAreaModal`) 미문서.
- **`text_utils.py` 설명 오도:** "한글/약어/회사명 처리" 라 했으나 실제로는
  re-export shim — 구현은 여전히 `screens/entries_compact.py` 에 물리적
  존재(`text_utils.py` 가 `from ...screens.entries_compact import ...`).
  즉 *비-screen 유틸 모듈이 screen 모듈에 역의존* — 기존 리뷰가 없애려던
  바로 그 inverted-dependency 냄새가 절반만 고쳐짐. CLAUDE.md 는 `entries_compact.py`
  를 아예 언급 안 해 LLM 이 구현 위치를 못 찾음.
- **처방:** 맵 재생성. `confirm_modal.py`→`confirm.py` 수정, 누락 파일 5종
  한 줄 역할 추가, `text_utils.py` 가 `entries_compact` 를 re-export 함을
  명기(또는 순수 헬퍼를 `text_utils.py` 로 실제 이동해 추출 완료).

### 1-C. [P2] 두 god 모듈은 거의 그대로

- **`client.py` (1340줄, +240): 타입 안정성 미개선.** 80 메서드,
  시그니처에 `Any` **86개**. `list_sections→list[dict[str,Any]]`,
  모든 report getter(`get_report`/`get_in_out`/`get_calendar`/`get_bill`/
  `get_checkcard`/`get_budget`)→`Any`(`client.py:1264-1288`). `WhooingClient`/
  `CachedWhooingClient` 가 전체 메서드 표면을 이중으로 들고 있어 엔드포인트
  추가 시 두 곳 편집 + 타입 단서 0. 최소비용 개선은 1-A 와 묶어 `responses.py`
  타입을 entry/account/report 반환에 부착.
- **`screens/entries.py` (3517줄, +517): 134 메서드 단일 클래스.** LLM 이
  안전 편집하기 가장 위험한 큰 메서드: `refresh_entries`(153줄, L3041, 3단계
  bootstrap + mid-flow return + 3중 에러처리), `_fetch_and_save_dupe_clusters`
  (147줄, L1234), `_scan_duplicates_worker`(120줄, L1114), `_expand_filter_in_past`
  (106줄), `_render_table`(83줄).
  - 부분 진전(인정): `EntryRepository` 추출은 실재·사용됨 — `_persist_local`/
    `_purge_local` 는 `self._repo` 위임. 다만 sqlite 가 repo 를 우회해 직접
    누수: `entries.py:1005, 2237, 2350, 3155` 에서 `tui_data.open_rw/open_ro`
    직접 호출(bulk-tag·cache-upsert 경로).
  - **처방:** dupe-scan worker 를 `dupe_scan_repo`/worker 모듈로 이동;
    bulk-tag·cache-upsert sqlite 를 `EntryRepository` 로 라우팅.

### 1-D. [P3] 신규 화면들의 패턴 분기 (저~중)

- **공유 `set_status` 없음** — `entries.py:3431`, `accounts.py:681`,
  `sections.py:167`, `dupe_eval.py:395` 가 각자 재정의(mixin 부재).
- **`notify` vs `set_status` 혼용** — 12 화면 `set_status`, 5 화면
  `self.notify`(budget_edit/receipt_attach/goal_edit/attachment_browser/
  monthly_entries), 그중 4개는 둘 다 사용. LLM 이 "이 코드베이스의 에러
  보고 방식" 을 pattern-match 못함.
- **처방:** `set_status` 를 `StatusScreenMixin` 으로 승격 + notify(transient)
  vs set_status(status bar) 사용 기준 문서화.

### 1-E. [P5] 사소

- 사용자 문자열의 하드코딩 `100` (`entries.py:1295,3424`, "100건 cap") —
  `_SERVER_PAGE_CAP`(=constants) 가 있는데 문자열은 안 따라감.
- 테스트 파일 없는 화면 3종: `dupe_scan_overview.py`, `revision_history.py`,
  `trash.py` — 그 외엔 `test_<screen>.py` 1:1 이라 LLM 이 의존하는 패턴이
  이 셋에서만 깨짐.

### LLM 측면 — 이미 좋은 점

`constants.py` 채택이 실재·깔끔(흩어진 `0xAC00`/`0xD7A3`/`365*5` 일원화).
모듈 docstring 이 이제 보편적이고 CL 번호·의도를 인용. 테스트 1:1 네이밍.
`EntryRepository`/`EntryRevisionRepository`/`DupeScanRepository` 추출 실사용.
CLAUDE.md 의 `함정` 섹션(Textual `_bindings` 충돌, `@work` race,
`WHOOING_DATA_DIR`) 은 LLM 이 드물게 얻는 implicit-coupling 함정 정보.

---

## 2. 보안

단일 사용자·로컬 신뢰 모델에서 **즉시 익스플로잇 가능한 HIGH 는 없다.**
SQL 전수 파라미터화, TLS 기본 on, 토큰 마스킹, `shell=True` 부재 등 위생이
양호하다. 실 항목은 "이메일로 수신한 카드명세서/첨부를 어디까지 신뢰하나"
에 달린 MED 들이다.

### 2-A. [MED] 카드명세서 HTML 을 네트워크 개방 Chromium 에서 렌더

`core/src/whooing_core/html_adapters/base.py:64-76`
```python
browser = await p.chromium.launch(headless=True)
context = await browser.new_context()      # ← 네트워크/JS 잠금 없음
page = await context.new_page()
...
await page.goto(file_url, wait_until="load")   # file_url = file://…공격자 HTML
await page.fill(password_input_selector, password)  # 사용자가 방금 입력한 암호
```
암호화 명세서 HTML 은 이메일로 도착 → 완전 공격자 제어. JS 활성·다이얼로그
자동 해제 상태의 실제 Chromium 에서 렌더되므로 악성 "명세서" 가 임의 JS 실행,
`file://` 리소스 fetch, 또는 **방금 `page.fill` 로 넣은 명세서 암호를 원격
beacon** 할 수 있다. 완화요인: 사용자가 의도적으로 명세서를 열고 암호를 입력.
- **처방:** context 를 offline 으로 띄우고(모든 non-`file:` scheme 요청
  `page.route` abort), 최소한 outbound 네트워크를 차단해 악성 명세서가
  입력 암호를 exfiltrate 못하게.

### 2-B. [MED] P4 changelist spec 에 첨부 파일명/태그 무검증 삽입

`tui/src/whooing_tui/p4_sync.py:202-206` — `description` 은 첨부
`original_filename`(`attachment_browser.py:191`), 해시태그명, item/memo
에서 조립된다. 본문은 `splitlines()` 로 들여쓰기되나, **개행을 포함한
파일명**(예: `x\nFiles:\n\t//depot/...`)은 동기화 spec 의 첫 합성 라인이
컬럼 0 에 떨어져 `p4` 가 새 form 필드로 파싱할 수 있다 → 제출 changelist 의
`Files:`/`Client:` 변조 가능. 입력→stdin 경로이며 완화요인은 "사용자가 그
파일을 직접 첨부".
- **처방:** spec 빌드 직전 `description`/`filename`/`tag` 에서 `\r\n\t`
  scrub, 또는 합성 라인이 P4 필드 키워드로 시작하지 않음을 검증.

### 2-C. [MED] MCP 에러 data 에 sanitize 부재

`tui/src/whooing_tui/official_mcp.py:131-143` — `OfficialMcpError(data=result)`
경로는 REST `client._request`(`client.py:136`, `sanitize_token` 적용)와
달리 요청/응답 body sanitize 가 없다. 서버가 `X-API-Key`/webhook 토큰을
에러 `data` 에 반사하면 unmasked 로 로그/표면 에러에 전파.
- **처방:** 예외에 저장 전 `errors.sanitize_for_log` 적용, 원본 `data` 를
  `ToolError` 텍스트에 넣지 않기.

### 2-D. [LOW] 신뢰불가 PDF 파싱 자원 무제한

`core/src/whooing_core/pdf_adapters/base.py:30-42`, `preview.py:115-143` —
`extract_all_tables`/`extract_all_text_lines` 가 페이지 수/시간 cap 없이
전 페이지 순회(`preview` 의 `cap_chars` 는 추출 *후* 적용). 조작된 PDF
(거대 페이지 수, pathological table)로 TUI hang/OOM. pdfplumber 자체엔
bomb 보호 없음.
- **처방:** 페이지 수 cap + timeout/size guard.

### 2-E. [LOW] 기타 하드닝

- `attachment_browser.py:61-84` `open`/`xdg-open` 은 argv list(좋음)지만
  OS 기본 앱에 첨부 파일을 넘김 — 이메일 수신 명세서를 ingest 하는 앱에서
  `.html`/`.command` 등은 foot-gun. 실행형 MIME 은 확인 후 open.
- `attachments.py:150` 쓰기는 `src.name`(basename)으로 traversal 안전.
  읽기/삭제(`purge_attachments_for_entry:328`, `delete_attachment:445`)는
  DB 의 `file_path` 를 root 에 join — 현재는 항상 `relative_to(root)` 라
  안전하나 방어적 `commonpath` 체크 없음. join 후 root 내부임을 assert.

### 보안 측면 — 견고한 점

SQL 전수 파라미터화(`db.py`/`attachments.py`/`dupe_scan_repo.py`, 동적
`IN(...)` 도 placeholder 개수만 f-string). `eval`/`exec`/`pickle`/
`yaml.load` 부재. `verify=False` 부재, 전 엔드포인트 `https://`, 합리적
timeout(REST 10s/MCP 30s)·rate-limit throttle·429 backoff. 토큰은
`X-API-Key` 헤더 전용(URL/query 미사용), `__repr__`/로그에서 마스킹
(`auth.py:28-35`, `errors.py:95-117`). `shell=True`/`os.system` 부재,
p4 바이너리는 `WHOOING_P4_BIN`/`shutil.which` 해석(상대 PATH 주입 차단).
BeautifulSoup 은 `html.parser` 사용(XXE 노출 없음).

---

## 3. 성능

대부분 Textual 단일 이벤트 루프 위에서 동작. P4 submit 이 per-mutation
subprocess 에서 종료시 단일 CL journal 로 이미 개선된 점은 좋다 — 남은
큰 항목은 스캔의 동기 실행과 선택 toggle 의 전체 재렌더다.

### 3-A. [P1] `find_duplicate_clusters` 가 이벤트 루프에서 동기 실행 → UI freeze

`tui/src/whooing_tui/screens/entries.py:1354`
```python
clusters = find_duplicate_clusters(entries, date_window_days=7)
```
worker 는 `@work(exclusive=True, group="dupe_scan")` 지만 **plain coroutine
(thread 아님)** — entries.py 전체에 `to_thread`/`thread=True` 없음. 함수는
순수 CPU(abs-money 버킷 → date-window two-pointer → pair별 NFKC normalize +
regex sub + `strptime` → union-find). 광고된 3년/~5000건 스캔에서 수백ms~수초
동안 키 입력·spinner 가 완전 멈춤 — 직전 `set_activity("🔍 …검색 중")` 도
루프가 즉시 점유돼 안 그려짐.
- **처방:** `clusters = await asyncio.to_thread(find_duplicate_clusters, entries, date_window_days=7)`.
  함수는 이미 순수(docstring 이 그렇게 명시)라 안전. 부수: `dupes.py:134`
  `_day_diff` 내부의 `from datetime import datetime` 을 모듈 레벨로 hoist.

### 3-B. [P1] 선택 toggle 마다 DataTable 전체 `clear()`+재빌드

`tui/src/whooing_tui/screens/entries.py:3298` (`_render_table`), 호출처:
`action_toggle_selection`(L968), `_extend_selection_to`(Shift 범위, L1638),
`action_row_up/down`(L1517,1534). 한 행의 `✅` 프리픽스 하나만 바뀌는데
**전 N행 재빌드**, 행마다 `_format_cell` 이 `title_of`×2 + `_abbreviate_account_name`×2
+ 첨부 dict + 태그 markup 조립. 1000행 창에서 스페이스 1회당 ~1000행 재빌드,
**Shift+Down 홀드 범위선택은 매 스텝 전체 재렌더 → O(N²)**. 컬럼 마커 경로는
이미 `update_cell_at`(L2021)로 올바르게 함.
- **처방:** 단일 toggle 은 해당 행만 `update_cell_at`; shift-range 는 멤버십이
  바뀐 행(delta)만 repaint. per-keystroke O(N)→O(1)/O(Δ).

### 3-C. [P2] 캐시 date 조회가 `substr()` 로 인덱스 무력화

`core/src/whooing_core/entries_cache.py:144-148`
```python
where.append("substr(entry_date, 1, 8) >= ?")
where.append("substr(entry_date, 1, 8) <= ?")
```
`idx_entries_cache_date` 는 `(section_id, entry_date DESC)`(`db.py:155`)인데
컬럼을 `substr()` 로 감싸 non-sargable → section 전 행 스캔 후 `_row_to_entry`
JSON 파싱. 필터 hot path(`_fetch_cache_extras`→`list_cached`, `entries.py:2240`)
에서 다년 캐시를 매 컬럼필터마다 스캔.
- **처방:** sub-index 접미는 `.` 뒤에만 오고 `entry_date` 는 8자 문자열이라
  bare 컬럼 lexical range 가능: `entry_date >= ?` / `entry_date < end+'.~'`
  (또는 정규화된 8자 `entry_date_head` 컬럼 추가) → 인덱스 사용.

### 3-D. [P2] 단일 mutation 마다 entries 윈도우 캐시 전체 무효화

`tui/src/whooing_tui/client.py:1224-1257` → `cache.py:163-170`
`invalidate_entries(section_id)` = `DELETE FROM entries_cache WHERE section_id=?`
— 해당 섹션의 **모든 (start,end) 윈도우** 폐기. create/update/delete 1회가
캐시 전체를 날려 다음 `list_entries` 는 cold refetch(20-rpm throttle 하 다중
paginated/bisect HTTP). bulk dedup 이 20-60건 순차 삭제(`entries.py:1064`)
시 캐시를 20-60회 비움 → 캐시 목적(`cache.py:5-7` docstring) 무력화.
- **처방:** mutation 의 `entry_date` 를 포함하는 윈도우만 선택 무효화, 또는
  TTL(이미 5분)에 맡기고 전체 삭제 지양.

### 3-E. [P3] `_combine_filter_results` 가 확장 스텝마다 전체 재정렬

`entries.py:2285`(`_combine_filter_results`, `_expand_filter_in_past` L2371
루프). 필터-확장 worker 가 3/6/12/24/60개월 스텝마다 `filter_entries` 재실행
+ 누적 리스트 full-sort + `_render_table` 전체 재빌드(3-B 재발). 5스텝이면
재필터·재dedup·재정렬·전체 재렌더 ×5. worker 라 hard-freeze 는 아니나 중복
O(M log M) + 5회 테이블 재빌드.
- **처방:** 누적 리스트를 정렬 유지하며 새 매치만 삽입(또는 루프 끝 1회 정렬);
  `_all_entries` 는 스텝 간 1회 필터.

### 3-F. 사소

- `dupes.py:134` `_day_diff` 의 per-call `datetime` import (스캔 후보쌍마다).
- `filters.py:120-136` `filter_entries` 가 후보 entry 마다 keyword set regex
  split 재계산 — entry별 keyword set 메모이즈 여지.

### 성능 측면 — 이미 좋은 점

worker 가 `@work(exclusive=True, group=…)` + epoch 가드로 cancel-at-await
race 처리. **P4 submit 이 per-mutation subprocess → 종료시 단일 CL journal
(`p4_sync.py:392` enqueue) 로 이미 이동 — write amplification 해소.**
bulk dupe 알고리즘은 설계상 sub-quadratic(bucket→two-pointer→union-find +
pair cache). 영구 `entries_cache` 행 테이블은 복합 인덱스 + 점진 upsert,
dupe-scan 결과 영구화로 재진입시 refetch 생략. 태그/첨부 batch read 는
`IN(placeholders)` 로 N+1 회피. 컬럼 마커는 targeted `update_cell_at`.
statement-import dedup 은 인덱스(`idx_import_entry_date`) 기반.

---

## 적용 우선순위 (체감 효과순)

1. **3-A** `asyncio.to_thread` 1줄 — 스캔 중 다초 freeze 제거.
2. **3-B** 선택 toggle targeted update — per-keystroke 랙(창 크기 비례) 제거.
3. **1-A / 1-B** 문구·맵 수정(저비용) — LLM 오도 즉시 차단.
4. **2-A** Playwright context offline — 입력한 명세서 암호 보호.
5. **2-B** P4 spec 직전 제어문자 scrub.
6. **3-C / 3-D** 캐시 인덱스/무효화 정상화.
