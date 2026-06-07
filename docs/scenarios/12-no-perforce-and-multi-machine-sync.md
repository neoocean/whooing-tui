# 12. Perforce 없는 환경 · 외부 SCM 없이 여러 기계에서 쓰기 (설계 문서)

> **상태: 동작 설명(현행) + 제안(미구현).** 1~3장은 **현재 코드의 실제
> 동작**, 4장 이후는 외부 형상관리 없이 다중 기계 동기화를 위한 **제안**.

## 목적

whooing-tui 는 후잉이 모르는 로컬 상태 — **메모·해시태그·첨부파일·수정
이력** — 을 로컬 SQLite(`db/whooing-data.sqlite`) + 첨부 디렉토리
(`attachment/`) 에 보관하고, **Perforce** 로 여러 기계 간 동기화한다
([09](09-startup-shutdown.md) / [10](10-storage-sqlite-vs-plaintext.md) /
[11](11-edit-history-and-soft-delete.md) 참조). 그렇다면:

1. **Perforce 가 없는 환경에선 어떻게 동작하나?**
2. **외부 형상관리도구(P4/git) 없이** db·첨부를 여러 기계에서 쓰려면?

## 1. 무엇이 Perforce 에 의존하나

| 데이터 | 위치 | 후잉이 아나 | 동기화 수단 |
|---|---|---|---|
| 거래 본체(날짜·금액·계정·item·memo) | 후잉 서버 | ✓ | 후잉(자동) |
| 메모·해시태그 | sqlite `entry_annotations`/`entry_hashtags` | ✗ | **P4** |
| 첨부파일(본체+메타) | `attachment/` + sqlite `entry_attachments` | ✗ | **P4** |
| 수정 이력·휴지통 | sqlite `entry_revisions`/`entry_head` | ✗ | **P4** |
| entries 캐시 | sqlite `entries_cache` | (후잉 미러) | P4(편의) |

즉 **거래 자체는 후잉이 동기화**하고, P4 는 *후잉이 모르는 로컬 부가데이터*
의 기계 간 동기화만 담당한다. (`p4_sync.py`)

## 2. Perforce 없는 환경에서의 동작 (현행 — graceful degradation)

**결론: 단일 기계에서는 완전히 정상 동작한다. 에러도, 차단도 없다.**
P4 는 *있으면 동기화, 없으면 조용히 건너뛴다*.

구체적으로 (`tui/src/whooing_tui/p4_sync.py`):

- `_p4_bin()` 이 `p4` 실행파일을 못 찾으면 `None` → 모든 submit 경로가
  `"no-p4"` 로 **silent skip**(로컬 쓰기는 이미 끝난 뒤라 데이터는 안전).
- `sync_db_from_p4()`(시작 시 호출)는 p4 부재 / 워크스페이스 매핑 외이면
  **조용히 return** — 그 뒤 `init_schema()` 등 로컬 작업은 그대로 진행.
- `data.py`: 데이터 경로는 `<project>/db`·`<project>/attachment` (또는 아래
  env override). 즉 **P4 워크스페이스가 아니어도** 로컬 파일로 다 동작.
- `WHOOING_DATA_DIR` 가 설정돼 있으면 `init_shared_schema` 가 P4 sync 를
  **아예 건너뛴다**(테스트 격리 + "P4 안 씀" 모드의 공통 경로).

따라서 P4 없는 노트북 한 대에서: 거래 입력/수정/삭제, 첨부, 해시태그, 수정
이력·휴지통·복원 — **전부 정상**. 잃는 것은 **다른 기계와의 자동 동기화**뿐.

> 첨부 추가/삭제·메모·이력 기록 시 내부적으로 P4 submit 을 *enqueue* 하지만
> (`submit_files_to_p4` → journal), flush 시 `_do_submit_multi` 가 `no-p4`
> 로 끝나 무해하다. 사용자에게 오류로 표면화되지 않는다.

## 3. 그래서 P4 없이 다중 기계가 문제인 지점

P4(또는 동급 동기화)가 없으면, 기계 A 에서 단 메모/태그/첨부/이력이 기계 B
에 **나타나지 않는다**(거래 본체는 후잉으로 동기화되므로 보임). 첨부 파일
실체(`attachment/`)와 sqlite 가 각 기계에 고립된다.

## 4. 제안 — 외부 형상관리 없이 다중 기계 동기화

핵심 통찰: 동기화 대상은 **딱 두 가지** — `db/whooing-data.sqlite` 1개
파일과 `attachment/` 디렉토리. 그리고 경로는 **환경변수로 재배치 가능**하다:

- `WHOOING_DATA_DIR` — db(+기본 첨부 위치)의 부모.
- `WHOOING_ATTACHMENTS_DIR` — 첨부 루트(별도 지정 시).

이 두 가지를 **이미 다중 기계로 동기화되는 위치**에 두면 P4/git 없이 끝난다.

### 방법 A — 클라우드/피어 동기 폴더 (권장, 즉시 가능, 코드 변경 0)

Dropbox·iCloud Drive·OneDrive·Google Drive·**Syncthing**·NAS 등 파일 동기
폴더 안에 데이터를 두고 env 로 가리킨다:

```sh
# 예) Syncthing/Dropbox 로 동기되는 폴더
export WHOOING_DATA_DIR="$HOME/Sync/whooing/data"          # db 가 여기에
export WHOOING_ATTACHMENTS_DIR="$HOME/Sync/whooing/attachment"
make run
```

- 각 기계에서 같은 env 를 설정. 동기 도구가 `whooing-data.sqlite` 와
  `attachment/**` 를 알아서 기계 간에 옮긴다.
- **장점**: 코드 수정 불필요. 오늘 바로 가능. P4 와 달리 GUI 동기.
- **단점/주의 (중요)**: SQLite 파일을 *블라인드 파일 동기* 로 옮기면
  **동시 쓰기 충돌** 위험이 있다. 안전 규칙:
  1. **단일 작성자**: 한 번에 한 기계에서만 whooing-tui 를 연다(다른 기계는
     닫은 뒤). 가계부는 보통 이 패턴이라 현실적.
  2. **종료 후 동기 대기**: 앱을 닫으면 WAL 이 메인 .sqlite 로 checkpoint
     된다(연결 close 시). 동기 도구가 `-wal`/`-shm` 까지 같이 옮기되, 가장
     안전한 건 **닫힌 상태에서 동기 완료를 기다렸다 다른 기계에서 열기**.
  3. **충돌 파일 처리**: Dropbox 의 `...conflicted copy...` 같은 파일이
     생기면 더 최신/큰 쪽을 채택(수동). Syncthing 은 버전관리로 복구 가능.
  4. 첨부는 append-only(내용주소 sha 파일)라 충돌이 드물다 — 위험은 주로
     sqlite 1개 파일.

### 방법 B — 첨부를 후잉 서버 네이티브 첨부로 (장기, 일부 코드 필요)

후잉은 거래에 **서버 측 첨부**를 지원한다(일부 거래에 `static.whooing.com`
URL 의 이미지가 보임). 첨부를 로컬 `attachment/` 대신 **후잉에 업로드**하면:

- 첨부는 후잉이 동기화 → 별도 도구 불필요.
- 동기화 대상이 sqlite(메모/태그/이력)로 줄어든다.
- **현황**: TUI 클라이언트에 첨부 업로드 API 가 **아직 없다**(로컬 sqlite
  방식 선택, [10] 참조). 후잉 REST/MCP 의 업로드 엔드포인트 연동이 필요한
  **향후 과제**. 메모는 이미 후잉 memo 로 미러된다.

### 방법 C — 내장 폴더 동기 백엔드 (권장 설계, 코드 필요)

`p4_sync` 를 **교체 가능한 동기 백엔드**로 일반화한다:

```
SyncBackend (인터페이스)
  ├─ P4Backend         (현행 — Perforce)
  ├─ FolderBackend     (지정 폴더로 atomic copy — 클라우드/NAS 가 동기)
  └─ NoopBackend       (동기 안 함 — 단일 기계)
```

- 선택은 `whooing-tui.toml` 의 `[sync] backend = "folder" | "p4" | "none"`
  + `[sync] folder = "~/Sync/whooing"`.
- **FolderBackend**: mutation 후 `db` + 바뀐 첨부를 대상 폴더로 **원자적
  복사**(임시파일 → rename), 시작 시 대상 폴더가 더 최신이면 가져오기.
  방법 A 와 같은 효과지만 **WAL checkpoint 보장 + 원자적 교체 + 머신ID/해시
  기반 최신성 비교**로 충돌을 코드 레벨에서 줄인다.
- 자동 감지: P4 워크스페이스면 P4Backend, `[sync].folder` 설정 시
  FolderBackend, 둘 다 없으면 NoopBackend.
- 장점: 사용자가 "외부 형상관리도구 없이" 요구를 정확히 충족(폴더만 있으면
  됨), 동시성 안전 로직을 앱이 책임.

### SQLite 다중 기계 안전 노트 (방법 A/C 공통)

- 이미 WAL 모드 + `busy_timeout=5000` 사용(`core/db.py`). 연결 close 시
  WAL 이 메인 파일로 checkpoint 되므로, **앱을 닫은 상태의 .sqlite 1개**가
  완결적 스냅샷이다 — 동기 단위로 적합.
- 진짜 동시(두 기계 동시 열기) 쓰기는 어떤 파일동기도 안전 보장 못 한다 →
  **단일 작성자 권장**. 충돌 시 후잉(거래 본체)은 무사하고, 잃을 수 있는 건
  한쪽의 메모/태그/이력 일부 → 영향 한정적.

## 5. 권장

- **지금 당장, 코드 0**: **방법 A** — `WHOOING_DATA_DIR` +
  `WHOOING_ATTACHMENTS_DIR` 를 Syncthing/Dropbox 폴더로. **단일 작성자**
  규칙만 지키면 충분.
- **견고하게 (구현 시)**: **방법 C** FolderBackend — 폴더 동기 + 앱이
  원자성/최신성/checkpoint 보장. P4 의존을 선택지로 격하.
- **방법 B** 는 첨부 동기를 후잉에 위임하고 싶을 때의 장기 옵션.

## 6. 관련 파일

| 책임 | 파일 |
|---|---|
| P4 동기화(있으면 sync, 없으면 no-p4 skip) | `tui/src/whooing_tui/p4_sync.py` |
| 데이터/첨부 경로 해석 + env override | `tui/src/whooing_tui/data.py` (`db_path`/`attachments_root`/`init_shared_schema`) |
| 설정(toml) — `[sync]` 추가 지점 | `tui/src/whooing_tui/config.py` |
| sqlite 연결(WAL/busy_timeout) | `core/src/whooing_core/db.py` |
| 첨부 저장(내용주소 sha) | `core/src/whooing_core/attachments.py` |

## 7. 향후 (미구현)

1. `[sync] backend` config + `SyncBackend` 추상화(방법 C).
2. 후잉 네이티브 첨부 업로드 연동(방법 B).
3. FolderBackend 의 머신ID·해시 기반 최신성 비교 + 충돌 알림 UI.
