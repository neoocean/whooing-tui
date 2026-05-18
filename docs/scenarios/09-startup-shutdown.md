# 09. 시작 · 종료 시 P4 동기화

## 목적

여러 머신에서 같은 후잉 가계부를 다루는 사용자가 메모/태그/첨부를
의도치 않게 잃지 않도록 **시작 시점에 P4 head 가 최신인지 확인** +
**종료 시점에 모든 변경 사항 submit** 를 보장한다.

## 정책

| 시점 | 동작 |
|---|---|
| **시작** | `_StartupCheckScreen` 푸시 → ① 로컬에 unsubmitted 변경 있으면 우선 submit → ② P4 head 보다 오래됐으면 종료. |
| **mutation** | 매 변경마다 fire-and-forget submit (`submit_db_to_p4`). 실패해도 UI 무영향. |
| **종료** | `_ShutdownModal` 푸시 → `flush_on_exit` (wait_for_pending + blocking submit) → exit. 종료 시퀀스 *취소 불가*. |

## 시작 흐름 (`_StartupCheckScreen`)

```
WhooingTuiApp.on_mount
  └─ push_screen(_StartupCheckScreen(), callback=_on_startup_check_done)
      ├─ Static "데이터베이스 상태를 확인합니다…"  ← 즉시 보임
      └─ worker @work(exclusive, group=startup)
          ├─ 1) has_pending_local_changes(db) ?
          │      └─ Yes → "로컬 변경 사항 submit 중…" + flush_on_exit (blocking)
          ├─ 2) is_outdated_vs_p4(db) ?
          │      └─ Yes → stage="outdated" + 빨간 안내 + 닫기 버튼
          │              └─ 닫기 → dismiss(False) → app.exit()  (rc 비-0)
          └─ 3) 통과 → dismiss(True) → EntriesScreen push

WHOOING_DATA_DIR set (테스트) → 모두 skip → 즉시 dismiss(True).
P4 부재 / 매핑 외 → 두 helper 가 False → 검사 skip.
```

## 종료 흐름 (`_ShutdownModal`)

```
사용자 q (EntriesScreen 의 action_back) → app.action_graceful_quit
  └─ push_screen(_ShutdownModal)
      ├─ Static "종료 중…"
      ├─ Static "(작업 목록 수집 중…)"  ← set_interval 0.25s
      │   └─ _refresh_tasks: app.workers (RUNNING) + p4_sync.pending_count()
      └─ worker _shutdown_worker
          └─ flush_on_exit(db) (blocking, thread executor)
              ├─ wait_for_pending() — 진행 중 submit thread join
              └─ blocking submit — 마지막 안전망
          └─ self.exit() → on_unmount 의 idempotent flush (no-op)

종료 시퀀스 취소 불가: BINDINGS 에서 escape / q / ctrl+c 모두 noop.
```

## 새 환경에서 처음 실행할 때

1. `make install` 후 처음 실행 시 P4 환경 + 매핑 양쪽 OK 이면 `p4 sync`
   가 head 의 db 를 받아옴. 다른 host 에서 만든 메모/태그/첨부가
   합쳐진 상태로 시작.
2. 매핑 외이거나 P4 부재면 빈 db 로 시작 — `core_db.init_schema(p)` 가
   스키마 v8 을 만든다.
3. `WHOOING_DATA_DIR` env 가 set 이면 본 모든 흐름 skip — 테스트 격리
   / 비-P4 데모용.

## 보안 정책

- `/db/` 와 `/attachment/` 디렉토리 통째 GitHub 미러 차단. P4 에는
  올라간다 (본인 host 내부 서버 + 매핑 사용자 본인 한정).
- 자세한 규칙: [`MEMORY.md §5.2`](../../tui/MEMORY.md).

## 트러블슈팅

- **"DB 가 P4 head 보다 오래됨"** 안내 후 종료 — 터미널에서
  `p4 sync <db_path>` 후 재시작. 충돌이 있으면 P4 resolve 흐름 따름.
- **`p4 submit` 30s 타임아웃** — `_do_submit` 의 timeout 정책. 네트워크
  지연 시 한 번 더 시도하거나 환경 점검.
- **종료 모달이 오래 떠있음** — `_refresh_tasks` 가 실행 중 worker /
  p4 thread 를 라이브 표시. 보통 0.5~수초. 분 단위면 p4 서버 응답 문제.

## 관련 코드

- [`app.py: _StartupCheckScreen`](../../tui/src/whooing_tui/app.py) —
  CL #52832+ 시작 검사 모달.
- [`app.py: _ShutdownModal`](../../tui/src/whooing_tui/app.py) —
  CL #52761+ / CL #52819+ 종료 모달 (라이브 commands 목록 + 취소 불가).
- [`p4_sync.py`](../../tui/src/whooing_tui/p4_sync.py) —
  `has_pending_local_changes`, `is_outdated_vs_p4`,
  `flush_on_exit`, `wait_for_pending`, `pending_count`.
- [`data.py: init_shared_schema`](../../tui/src/whooing_tui/data.py) —
  처음 시작 시 `sync_db_from_p4` + 스키마 init.
