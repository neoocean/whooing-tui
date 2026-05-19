# 시나리오 10 — SQLite 저장소 vs plaintext (markdown / JSON) 검토

> **상태**: 검토 문서 (decision-not-yet-made). 현재 구현은 **SQLite 유지**.
> 본 문서는 plaintext 전환 시 장단점을 정량/정성적으로 비교, 향후 결정의
> 근거.

## 1. 배경 — 우리는 무엇을 sqlite 에 저장하는가

후잉 (whooing.com) REST API 는 **거래내역의 핵심 필드만 정식 모델로 노출**
한다. TUI 가 추가로 제공하는 다음 기능들은 후잉이 모르는 상태 — 우리가
**별도로** 보관해야 한다:

| sqlite 테이블 | 무엇을 보관 | 누가 write | 후잉이 모름 |
|---|---|---|---|
| `entry_annotations` | 거래별 사용자 메모 (1:1) | TUI | ✓ |
| `entry_hashtags` | 거래별 해시태그 (1:N) | TUI | ✓ |
| `entry_attachments` | 거래 ↔ 첨부 파일 (1:N) — sha256 dedup | TUI | ✓ |
| `statement_import_log` | 카드 명세서 import 이력 + dedup 기록 | TUI | ✓ |
| `entries_cache` | 후잉 거래내역 영구 캐시 (점진 확장 윈도우) | TUI | (캐시) |
| `schema_meta` | 스키마 버전 | TUI |  |

본체 파일은 `<project_root>/db/whooing-data.sqlite`. 첨부 본체는 별도
디렉토리 (`attachment/YYYY/YYYY-MM-DD/<sha256>.<ext>`) — sqlite 는
relative path 만 보관.

P4 가 sqlite + 첨부 디렉토리를 함께 submit (CL #52727+ 정책).
`.gitignore` 가 GitHub 미러로의 누출을 막음.

기록량 (2026-05-19 현재 실 운영 기준): annotations ~수백 행, hashtags
~수백 행, attachments ~수백 행 + 디스크 ~수십 MB, import_log ~수천 행,
entries_cache ~수천 행. **총 sqlite 파일 < 10 MB**, 첨부 본체는 별도
~수백 MB.

## 2. 대안 — plaintext 포맷

검토 후보:

| 포맷 | 인간 가독성 | diff 친화 | 구조화 | 쿼리 도구 |
|---|---|---|---|---|
| **JSON** (jsonl 한 줄/거래) | 보통 | 좋음 | 강 | `jq` |
| **JSON** (한 파일에 전체) | 나쁨 (수만 줄 single object) | 나쁨 | 강 | `jq` |
| **TOML** (per-section) | 좋음 | 좋음 | 중 | tomlkit |
| **Markdown** (per-entry front-matter + body) | 매우 좋음 | 매우 좋음 | 약 | grep / 자체 파서 |
| **CSV** (per-table) | 좋음 | 보통 | 약 | awk / 자체 파서 |

본 검토는 **하이브리드** (annotations + hashtags 는 markdown / json,
첨부 메타와 import_log 는 csv) 도 포함.

## 3. 비교 표 — 기능별

| 항목 | SQLite (현재) | jsonl 1줄/거래 | markdown per-entry |
|---|---|---|---|
| 쓰기 latency | µs 단위 | ms (파일 잠금 + 동기화) | ms × N (1거래 = 1파일 fsync) |
| 읽기 latency (1건) | < 1 ms | ~10 ms (전체 스캔 + line index) | < 1 ms (파일 stat) |
| 읽기 latency (3년 1000건) | ~10 ms | ~30 ms | ~수 초 (1000 fopen) |
| 트랜잭션 | 내장 (BEGIN/COMMIT) | 없음 — 부분 쓰기 위험 | 없음 — 부분 쓰기 위험 |
| 동시 쓰기 | WAL 모드로 안전 | 별도 lock 필요 | 별도 lock 필요 |
| 인덱싱 (해시태그 lookup) | `CREATE INDEX` 즉시 | 매 쿼리마다 풀스캔 | 매 쿼리마다 풀스캔 |
| schema migration | `ALTER TABLE` + version check | 명세 변경 시 모든 파일 lazy-rewrite | 동일 |
| Backup (P4 submit) | 1 binary file | 수많은 작은 파일 (또는 1 큰 파일) | 거래 수만큼 파일 |
| Diff 가독성 | binary, 도구 필요 | jsonl 한 줄 diff | markdown 친화 |
| 직접 편집 | sqlite3 CLI / DBeaver | 텍스트 에디터 / jq | 텍스트 에디터 |
| 손상 복구 | sqlite3 .recover | 라인 단위 잘라낸 뒤 재파싱 | 손상 파일 1개만 손실 |
| 외부 도구 통합 | python sqlite3 (표준 라이브러리) | python json (표준) | 정규 표현식 / 자체 파서 |
| 검색 (full-text on note) | FTS5 추가 가능 | 풀스캔 | 풀스캔 (grep) |
| 동시 backup 안전 | sqlite-backup (online) | rsync (split-brain 가능) | rsync (split-brain 가능) |
| 외부에서 분석 | duckdb / 직접 SQL | jq, pandas | bash, ripgrep |

## 4. plaintext 의 *장점*

### 4.1 인간 가독성 / 직접 편집

`db/whooing-data.sqlite` 는 사용자가 직접 열어볼 수 없음. `sqlite3` CLI
또는 GUI 도구 필요. 반면:

- markdown: `vim`, `code`, 또는 GitHub 미러에서 바로 읽힘.
- jsonl: `jq` 한 줄로 필터/추출.

특히 **5-10년 후** 본 TUI 가 archived 됐을 때 데이터 접근성이 다름:
sqlite 의 binary 는 도구 의존, plaintext 는 영원.

### 4.2 git/P4 diff 의 의미

CL submit 마다 sqlite binary diff → P4 가 `binary` 로 표시, diff 못 봄.
plaintext 면 어떤 거래의 어떤 필드가 바뀌었는지 review 가능.

예 (markdown per-entry):
```diff
--- entry/2026/2026-05-19/1419536.md
+++ entry/2026/2026-05-19/1419536.md
@@ -3,4 +3,5 @@
 tags: [점심, 한식]
+memo: 김부장과 미팅
```

지금은 `db/whooing-data.sqlite` 가 한 binary 통째 — *어떤* 거래가 바뀌었는지
review 단계에서 모름.

### 4.3 손상의 격리

sqlite 가 손상되면 (drive 불량, OOM-kill 중 fsync 누락) 전체 데이터에
영향. plaintext 는 손상 파일 한 개만 잃음 — N-1 거래는 살아있음.

### 4.4 외부 도구 / 자동화 통합

- 사용자가 명령줄 grep 으로 모든 해시태그 호출
- LLM 에 markdown 전체 dump 를 그대로 feed
- diff/patch 로 bulk edit
- crontab + git commit 으로 자동 backup

sqlite 로는 모두 한 단계 더 (export → 가공) 필요.

### 4.5 P4 submit 단위 작아짐

현재 매 mutation 마다 `db/whooing-data.sqlite` 통째 (< 10 MB) submit.
잦은 commit → P4 ledger 부풀림. plaintext 면 *바뀐 거래 파일만* submit.

## 5. plaintext 의 *단점*

### 5.1 트랜잭션 부재 → race / 부분 쓰기

sqlite 의 `BEGIN ... COMMIT` 은 ACID. 두 작업 (예: 거래 입력 + 해시태그
추가) 이 한 단위로 원자적. plaintext 는:

- 한 파일을 atomic-write (write→rename) 으로 처리하면 1 파일 atomic OK.
- 여러 파일 동시 update 면 별도 lock 필요.
- 우리 경우 첨부 추가 = `entry_attachments` 1 row + 파일 1개 — sqlite
  로는 BEGIN 후 commit, plaintext 면 mv + rewrite manifest 같은
  순서 보장 필요.

**TUI 가 worker 로 비동기 처리** 하므로 race 위험 실재. 카드 명세서
일괄 import 가 50건 동시 진행되면 plaintext 는 정합성 보장 어려움.

### 5.2 인덱싱 — 검색 성능

해시태그 검색 ("#점심 으로 태그된 모든 거래") 는 현재 sqlite 가
`idx_hashtags_tag` index 로 O(log N). plaintext 는 전체 스캔 O(N).

연간 1000 거래 × 3년 = 3000 거래에서:
- sqlite: < 1 ms
- jsonl: ~30 ms (3000 line 풀스캔 + JSON 파싱)
- markdown: ~수 초 (3000 파일 stat + grep)

지금 TUI 의 자동 완성, 빠른 필터, 거래내역 검색 같은 *interactive* 경험은
plaintext 로 가면 *유의미하게* 느려진다 (사용자 입력 한 글자마다 30 ms +).

### 5.3 schema migration

sqlite v4 → v5 → ... → v8 까지 `ALTER TABLE` 로 점진. plaintext 는
모든 파일을 lazy 또는 eager rewrite. 5년치 거래 ~5000 파일을 한 번에
변환하는 stop-the-world migration 이 필요.

### 5.4 첨부 메타데이터 — 1:N 관계의 자연스러움

`entry_attachments` 는 한 거래에 여러 첨부 ↔ 한 파일이 여러 거래에 공유
(sha256 dedup). 1:N + 역참조 모두 필요. plaintext 로 표현하려면:

- per-entry markdown 에 attachment paths 배열.
- 그러나 "이 파일이 어느 거래 들에 attached?" 역참조 = 전체 스캔.

sqlite 는 자연스러운 JOIN, plaintext 는 보조 manifest 가 또 필요해 결국
다층 indirect.

### 5.5 동시 backup — split-brain

P4 submit 또는 rsync 가 한창 진행 중인데 TUI 가 새 거래를 write 하면
sqlite 는 WAL 로 lock 보장. plaintext 는 일부만 동기화된 inconsistent
state 가 백업으로 떨어질 수 있음.

회피 가능 — TUI 가 backup window 동안 write 보류. 하지만 코드 복잡도 ↑.

### 5.6 파일 수 폭증

5년치 plaintext 면 5000 ~ 10000 개의 작은 파일. macOS Finder / VS Code
의 인덱싱이 느려짐. P4 가 trace 하는 file count 도 비대.

`db/whooing-data.sqlite` 한 개 + `attachment/` 디렉토리 한 개로 사실상
**2 tracked node** 인 현재 구조가 깔끔.

### 5.7 LLM / Claude Code 컨텍스트 비용

본 monorepo 는 LLM 친화를 강하게 의식 (CLAUDE.md, scenarios/, MAINTAINABILITY-
REVIEW.md). plaintext 면 *모든 거래 파일* 이 LLM 의 search 결과로
나타날 수 있어 컨텍스트 오염. sqlite 는 단일 binary 라 검색에서 자동 skip.

> 역으로 — 작은 케이스 (예: "지난주 점심 거래 보여줘") 는 plaintext 가
> 훨씬 자연스럽다. **trade-off 양면이 모두 존재.**

### 5.8 첨부 본체 위치는 어차피 plaintext

이미 첨부 본체는 `attachment/YYYY/YYYY-MM-DD/<sha256>.<ext>` 로 파일
시스템 native. **plaintext 화의 가장 큰 효용** (외부 도구 통합, 직접
열람) 은 이미 첨부에서는 누리고 있음. sqlite 가 보관하는 건 path string
뿐. 메타데이터를 plaintext 화해도 *추가* 효용이 크지 않음.

## 6. 하이브리드 — 부분 도입

**가장 매력적인 안**: `entry_annotations` (메모) + `entry_hashtags` (태그)
만 markdown 으로 빼고, 나머지 (entries_cache, statement_import_log,
entry_attachments) 는 sqlite 유지.

```
db/
├── whooing-data.sqlite       # 캐시 / import 로그 / 첨부 메타
└── annotations/
    └── 2026/
        └── 2026-05-19/
            └── 1419536.md     # entry-1419536 의 메모 + 태그
```

`1419536.md`:
```markdown
---
entry_id: 1419536
section_id: s9046
tags: [점심, 한식, 회식]
created_at: 2026-05-19T12:34:56+09:00
updated_at: 2026-05-19T18:00:00+09:00
---

김부장과 점심. 다음주 일정 논의.
참석: 김부장, 나, 정대리.
```

### 6.1 하이브리드의 장점

- 사용자가 **사람이 쓴 내용** (메모/태그) 만 plaintext 로 — 직접 편집,
  diff review 가치 큰 부분만 격리.
- 캐시 / 로그처럼 *기계가 채우는* 데이터는 sqlite 유지 — 성능 + 트랜잭션.
- 첨부 본체와 메타가 자연스럽게 같은 계층 (`attachment/` / `annotations/`).
- migration 부담 작음 — sqlite v9 = "annotations 컬럼 제거" 한 번.

### 6.2 하이브리드의 단점

- 두 시스템 — `entry_annotations` 가 markdown 으로 갔다고 코드가 단순해
  지지 않음. 양쪽 다 읽고 쓰는 layer 필요.
- 같은 entry_id 의 메모를 sqlite + md 양쪽이 갖고 있으면 동기화 문제.
  → 단방향 (md = source of truth, sqlite = projection) 으로 풀어야.
- 태그 검색 성능 (#점심) 은 plaintext 로 가면 여전히 느려짐 — 별도
  인덱스 캐시 필요 (= 결국 sqlite).

## 7. 권고 — *지금은 옮기지 않는다*

| 기준 | 현재 sqlite | 옮길 만한 trigger |
|---|---|---|
| 인터랙티브 성능 | 충분히 빠름 | 사용자가 latency 호소 시 |
| 데이터 수명 / 영원 접근성 | 도구 필요 | 본 TUI 가 EOL 결정 시 |
| diff/review 가치 | review 잘 안 함 | 협업자 늘어나면 |
| migration / schema 안정 | v8, 안정 | major schema 변경 필요 시 |
| 손상 위험 | WAL + P4 = 충분 | 손상 실제 사례 발생 시 |
| 외부 도구 통합 | 거의 안 함 | grep / LLM dump 가 일상화될 때 |

**현재 상태에서 plaintext 전환의 ROI 는 낮다.** 핵심 사용자 (1인) 가
sqlite 의 가독성/편집 부재를 절감하지 않는 한, 마이그레이션 코스트 (테스트
재작성, race 처리, 성능 회귀) 가 효용을 초과.

### 7.1 점진 trigger — 미래에 *재검토* 할 신호

1. 사용자가 GitHub 미러에서 본인 거래내역 메모를 "읽을 수 있게 해 달라"
   고 요청. → annotations 만 md 화 (하이브리드 6.1).
2. sqlite 파일이 100 MB 초과. → 정기 archive (오래된 import_log 분리).
3. 또 다른 클라이언트 (모바일 / 다른 LLM 도구) 가 같은 데이터를 read 해야
   하면. → 표준 포맷 (jsonl) export 도구 추가.

### 7.2 *지금 당장* 할 수 있는 작은 개선

plaintext 화 없이도 현재 sqlite 의 단점을 완화하는 안:

- `tui/tools/dump-annotations.py` — sqlite 의 annotations + tags 를
  markdown 으로 export (read-only, P4 미커밋, 백업 용도). 한 번씩
  돌려서 plaintext 사본 확보.
- P4 submit 메시지에 사용자가 입력한 *최근* annotation 요약 첨부 — review
  단계에서 diff 가독성 보완.
- sqlite browser 도구 (Sqliteview, Beekeeper Studio) 사용법을
  `docs/scenarios/` 에 명문화.

## 8. 종합

- **결정** (2026-05-19): SQLite 유지.
- **이유**: 본 TUI 의 인터랙티브 latency 요구, 트랜잭션 안전성, 1인
  소규모 사용 패턴이 sqlite 의 강점과 잘 맞는다. plaintext 의 가독성 /
  직접 편집 / diff review 효용은 *현재* 사용 패턴에서 핵심이 아니다.
- **재검토 시점**: 위 §7.1 의 trigger 중 하나라도 발생 시 본 문서 갱신.
