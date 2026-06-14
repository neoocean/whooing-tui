# 12. Perforce 없는 환경 · 외부 SCM 없이 여러 기계에서 쓰기 (설계 문서)

> **상태: 동작 설명(현행) + 제안(일부 미구현).** 1~3장은 **현재 코드의 실제
> 동작**, 4장 이후는 외부 형상관리 없이 다중 기계 동기화를 위한 **제안**.
>
> **0.85.0 (2026-06): 동기화 백엔드가 facade(`sync.py`)로 분리되고 opt-in
> 으로 바뀜.** 기본 백엔드는 **`none`(동기화 안 함)** — P4 를 쓸 수 있는
> 사용자만 켠다. 코어는 `sync` facade 만 호출하고 `p4_sync` 를 직접 모른다.

## 0. 동기화 백엔드 켜고 끄기 (0.85.0+)

머신 간 동기화는 **선택적 백엔드**다. 결정 우선순위는 **env >
config > 기본(`none`)**:

```toml
# whooing-tui.toml  (또는 ~/.config/whooing-tui/config.toml)
[sync]
backend = "p4"     # "none"(기본, 동기화 안 함) | "p4"(Perforce)
```

```sh
# 또는 환경변수로 (config 보다 우선)
export WHOOING_SYNC_BACKEND=p4
```

- **`none`(기본)**: 모든 동기화 동작이 no-op. 시작 시 P4 검사 splash 도 안
  뜨고, 종료 시 flush 도 없고, mutation 후 submit 도 알림도 없다 — **P4 를
  못 쓰는 사용자는 이 기능을 완전히 무시**. 단일 기계 로컬 사용은 100% 정상.
- **`p4`**: 종전과 동일하게 Perforce 자동 submit/sync (아래 1~3장, [09] 참조).
  단, p4 CLI + workspace 가 실제로 갖춰져 있어야 의미가 있다(없으면 백엔드는
  켜져 있어도 `p4_sync` 내부에서 graceful skip).
- 새 백엔드(예: folder — §4-C)는 `sync.py` facade 함수에 분기만 추가하면 되고
  코어 호출부(app/data/repository/screens/cli)는 손대지 않는다.

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

즉 **거래 자체는 후잉이 동기화**하고, 동기화 백엔드는 *후잉이 모르는 로컬
부가데이터* 의 기계 간 동기화만 담당한다. 코어는 `sync.py` facade 만 호출하고,
P4 는 그 뒤의 한 백엔드(`p4_sync.py`)다.

## 2. Perforce 없는 환경에서의 동작 (현행 — graceful degradation)

**결론: 단일 기계에서는 완전히 정상 동작한다. 에러도, 차단도 없다.**

- **백엔드 `none`(기본, 0.85.0+)**: `sync.*` 가 전부 no-op. 시작 검사·종료
  flush·mutation submit·알림 모두 발생 안 함. P4 설치 여부와 무관하게 코어가
  그대로 동작 — **P4 를 못 쓰는 사용자는 이 기능을 완전히 무시**.
- **백엔드 `p4` 인데 p4 환경이 불완전**: 켜 두어도 `p4_sync` 가 graceful
  skip. `_p4_bin()` 이 `p4` 를 못 찾으면 submit 은 `"no-p4"` 로 silent skip,
  `sync_db_from_p4()` 도 조용히 return(로컬 쓰기는 이미 끝나 데이터 안전).
- `data.py`: 데이터 경로는 `<project>/db`·`<project>/attachment` (또는 아래
  env override). 즉 **P4 워크스페이스가 아니어도** 로컬 파일로 다 동작.
- `WHOOING_DATA_DIR` 는 이제 **데이터 격리**만 담당(테스트 tmp 등) — P4 skip
  여부는 백엔드 설정으로만 결정된다(종전엔 둘이 얽혀 있었음).

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

### 방법 C — 내장 폴더 동기 백엔드 (facade 는 구현됨, FolderBackend 는 향후)

동기 백엔드는 **0.85.0 에서 facade(`sync.py`)로 일반화됨**:

```
sync.py (facade)  ← 코어는 이것만 호출
  ├─ "p4"    → p4_sync (구현됨 — Perforce)
  ├─ "none"  → no-op   (구현됨 — 기본, 단일 기계)
  └─ "folder"→ FolderBackend (향후 — 지정 폴더로 atomic copy)
```

- 선택: `whooing-tui.toml` 의 `[sync] backend = "none" | "p4"` (+ 향후
  `"folder"` 와 `[sync] folder = "~/Sync/whooing"`), env `WHOOING_SYNC_BACKEND`
  가 우선. **기본은 `none`** — auto-detect 안 함(P4 가 우연히 설치돼 있어도
  명시 opt-in 전에는 켜지지 않음).
- **FolderBackend(향후)**: mutation 후 `db` + 바뀐 첨부를 대상 폴더로 **원자적
  복사**(임시파일 → rename), 시작 시 대상 폴더가 더 최신이면 가져오기.
  방법 A 와 같은 효과지만 **WAL checkpoint 보장 + 원자적 교체 + 머신ID/해시
  기반 최신성 비교**로 충돌을 코드 레벨에서 줄인다. facade 함수에 분기만
  추가하면 되고 코어 호출부는 불변.

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
| **동기화 facade** — 백엔드 선택(none/p4) + no-op/위임 | `tui/src/whooing_tui/sync.py` |
| P4 백엔드(있으면 sync, 없으면 no-p4 skip) | `tui/src/whooing_tui/p4_sync.py` |
| 데이터/첨부 경로 해석 + env override | `tui/src/whooing_tui/data.py` (`db_path`/`attachments_root`/`init_shared_schema`) |
| 설정(toml) — `[sync] backend` | `tui/src/whooing_tui/config.py` |
| sqlite 연결(WAL/busy_timeout) | `core/src/whooing_core/db.py` |
| 첨부 저장(내용주소 sha) | `core/src/whooing_core/attachments.py` |

## 7. 향후 (미구현)

1. ~~`[sync] backend` config + 동기 백엔드 추상화~~ → **0.85.0 구현**
   (`sync.py` facade, `none`/`p4`). 남은 것은 `folder` 백엔드 추가.
2. 후잉 네이티브 첨부 업로드 연동(방법 B).
3. FolderBackend 의 머신ID·해시 기반 최신성 비교 + 충돌 알림 UI.
