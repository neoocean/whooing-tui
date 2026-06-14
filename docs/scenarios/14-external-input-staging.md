# 시나리오 14 — 외부입력(임시저장소) 조회·확정

> **구현 상태 (2026-06-14)**: **구현됨** — `outside.py`(클라이언트) +
> `screens/outside_inbox.py`(화면) + 테스트 17건. 단, **메뉴 wiring(`entries.py`)
> 은 0.85.0 동기화 facade WIP 와 섞이지 않도록 현재 로컬 작업트리에만** 두었고
> (P4 미제출), 0.85.0 확정 후 정식 배선한다. 즉 모듈은 P4 에 있으나 진입
> 메뉴는 아직 P4 에 없다.

## 목적

후잉 **외부입력 = 임시저장소**(카드·은행 SMS 가 이메일/웹훅으로 들어왔지만
아직 장부에 *확정되지 않은* 항목)를 TUI 에서 조회하고, 계정을 지정해 일괄
확정(거래 입력)하거나 비운다. 지금까지 임시저장소 처리는 후잉 **웹 UI(단축키
`o`)에서만** 가능했다 — 카드 명세서 import(시나리오 04)가 *명세서 파일* 을
다룬다면, 본 기능은 *후잉이 이미 파싱해둔 SMS 스테이징* 을 다룬다.

## 배경 — 왜 별도 기능인가

- 카드/은행 SMS 를 후잉 웹훅(`https://whooing.com/webhook/s/{section-token}`)
  또는 이메일(`i-…@whooing.com`)로 보내면, 후잉이 파싱해 **임시저장소**에
  쌓는다. 인식된 항목은 사용자가 웹에서 상대계정을 확인해 **확정**해야
  비로소 장부 거래가 된다(인식 실패분은 신고 대상).
- 공식 OpenAPI 는 임시저장소에 대해 **쓰기 전용**만 노출한다:
  - `POST entries/outside.json` — SMS 원문(`rows`)을 임시저장소에 제출.
  - `POST entries/outside_report.json` — 인식 실패한 소스 신고(`source`).
  - 임시저장소를 **조회(목록)** 하는 공식 GET 은 **없다**.
- 그러나 후잉 웹 UI 의 임시저장소 모달(`main_insert_outside.js`)이 내부적으로
  쓰는 **오버로드된 호출**로 목록을 읽을 수 있고, **X-API-KEY 인증이 통함**을
  확인했다(2026-06-14, 직접 검증).

## 접근 메커니즘 (후잉 비공식 내부 엔드포인트)

| 동작 | 요청 | 파라미터 / 비고 |
|---|---|---|
| **조회(목록)** | `POST /api/entries/outside.json` | `section_id`, **`rows=`(빈 값=읽기 신호)**, `ids=out_id`, `omax_id=`(페이징 커서), `m=n` → `results.outdata[]` |
| ~~개수~~ | ~~`GET /api/main/outside_count`~~ | **X-API-Key 로는 403** — `/api/main/*` 은 웹 세션 전용 네임스페이스. 대기 건수는 조회 목록 길이로 대신한다(2026-06-14 확인). |
| **확정(입력)** | `POST /api/entries.json` | `section_id`, `entries=<JSON 배열>`, `del_ids=<out_id 콤마결합>` → 거래 생성 + 해당 out_id 제거(원자적) |
| 제출(SMS 적재) | `POST /api/entries/outside.json` | `rows=<SMS 원문>` *(공식 문서화됨)* |
| 비우기 | `POST /api/entries/empty_outside.json` | 임시저장소 전체 비움 |
| 파일 업로드 | `POST /api/main/outside_from_file` | 엑셀 등 파일 일괄 |

- **읽기/쓰기 구분의 핵심**: `rows` 가 **빈 값**이면 아무것도 입력하지 않고
  목록만 돌려준다(read-only). `rows` 에 SMS 원문이 있으면 제출(쓰기)이다.
- **페이징**: `ids=out_id` 정렬에서 `omax_id` 에 *직전 응답의 마지막 `out_id`*
  를 넣어 다음 페이지를 받는다(한 번에 약 100건). 웹 UI 는 "더 불러오기"가
  같은 호출을 반복한다.
- **`results.outdata[]` 한 행의 모양**(필드명은 후잉 내부 표현):

  | 필드 | 의미 |
  |---|---|
  | `out_id` | 임시저장소 행 고유 ID(확정/삭제 시 키) |
  | `entry_date` | 거래일 `YYYYMMDD` |
  | `money` | 금액(해외건은 외화 액면일 수 있음) |
  | `right` | 후잉이 추정한 출처(예: 카드 표기 `…카드(2*9*)`) |
  | `r3` | 가맹점/적요 |
  | `r5` | 승인 시각 |
  | `raw` | 파싱 전 **SMS 원문 전체** |
  | `detail` | 괄호 detail 후보 |
  | `r` | 추정 상대계정(예: `liabilities_x80`) |
  | `app_id` | 입력 소스 앱 식별 |

> **주의**: 위 조회/개수/비우기 경로는 **후잉 비공식·내부 엔드포인트**다(공식
> OpenAPI 에 없음). 후잉이 예고 없이 바꿀 수 있으므로, 호출부는 실패를
> graceful 하게 처리하고 사용자에게 "후잉 내부 변경 가능" 을 안내한다.

## 주요 경로

`입력` 메뉴 → `외부입력(임시저장소) 조회…`(action `scan_outside`)
→ 목록 화면(`OutsideInboxScreen`) → 행 선택 후
`Enter`/`c` 확정(입력) / `d` 삭제 / `e` 전체비우기 / `F5` 새로고침 / `Esc` 닫기.

## 단계별 흐름

1. **목록 fetch** — 진입 시 `OutsideClient.list_all(section_id)`(=오버로드
   POST, `rows=`)로 전체 적재(100건 초과면 `omax_id` 로 페이지네이션).
   대기 건수 = 목록 길이(별도 count 엔드포인트는 API 키로 403).
2. **표시** — 각 행을 날짜/금액/출처(카드)/가맹점/추정 상대계정으로 표시.
   `r`(예: `liabilities_x80`)을 대변으로 해석.
3. **확정(입력)** — `Enter`/`c` → AccountPicker 로 차변(지출) 계정 지정
   (시나리오 02 패턴) → `OutsideClient.confirm` 가 `entries.json`
   (`entries`+`del_ids`)로 거래 생성 + 해당 `out_id` 제거(원자적) → 목록에서
   제거.
4. **삭제** — `d` → 확인 모달 → `OutsideClient.delete`(빈 `entries`+`del_ids`)
   로 장부 입력 없이 임시저장소에서 제거.
5. **전체 비우기** — `e` → 확인 모달 → `OutsideClient.empty`
   (`empty_outside.json`). 되돌릴 수 없음.

## 카드 명세서 import(시나리오 04)와의 관계

- 시나리오 04 는 *카드사 명세서 파일(CSV/HTML/PDF)* 을 직접 파싱한다(자체
  어댑터). 본 시나리오는 *후잉이 이미 파싱해 쌓아둔 SMS* 를 가져온다 —
  소스가 실시간 SMS 라 명세서 확정 전에도 즉시 처리 가능.
- 두 경로가 같은 거래를 만들 수 있으므로(SMS 즉시 + 명세서 사후), 중복
  탐지(시나리오 05)와 함께 쓰는 것을 전제로 한다.

## 보안 / 프라이버시

- `raw` SMS 원문에 카드 끝자리·누적 잔액 등 민감정보가 포함된다 → 로그에는
  `errors.sanitize_for_log` 로 마스킹, 화면 표시만 원문.
- X-API-KEY 는 `.env`(또는 `~/.config/whooing/.env`)에서만 로드.

## 관련 코드

- `tui/src/whooing_tui/outside.py` — standalone `OutsideClient`
  (`list`/`list_all`/`confirm`/`delete`/`empty`) + 순수 헬퍼
  (`parse_counter_account`/`build_entry`/`staged_item_text`). client.py 비의존
  (official_mcp.py 처럼 httpx + WhooingAuth 직접). 실패는 `OutsideError`.
- `tui/src/whooing_tui/screens/outside_inbox.py` — `OutsideInboxScreen`
  (목록 + 확정/삭제/비우기 worker).
- `tui/src/whooing_tui/screens/entries.py` — `action_scan_outside` + "입력"
  메뉴 항목 `외부입력(임시저장소) 조회…`. **(0.85.0 WIP 분리 위해 현재 로컬
  작업트리만; P4 미제출 — 0.85.0 확정 후 정식 배선)**.
- (참고) 후잉 웹 번들 `static.whooing.com/assets/new/render/
  main_insert_outside.js` — 위 엔드포인트의 원천(`loadFromTemporary` 가
  `rows=""` 로 조회, 확정은 `entries.json` 의 `entries`+`del_ids`).

## 테스트

- `tui/tests/test_outside.py` — `OutsideClient`(respx) + 순수 헬퍼 (9건):
  오버로드 POST 가 `rows=""` 로 조회, 확정/삭제 요청 형태, 비-200 graceful.
- `tui/tests/test_outside_inbox.py` — 화면 worker (조회 렌더/확정/삭제/비우기,
  8건). 실제 라이브 조회는 read-only 로 검증(확정/삭제는 실데이터 보호 위해
  미발화).
