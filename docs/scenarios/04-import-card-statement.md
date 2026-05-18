# 04. 카드 명세서 일괄 가져오기

## 목적

카드사 명세서 (CSV / HTML / PDF) 한 파일을 통째로 import — 어댑터가
파싱 → 후잉 ledger 와 dedup → 신규만 일괄 입력.

## 주요 경로

```
EntriesScreen
  └─ 메뉴 → 입력 → 카드 명세서 import…
      └─ FilePathModal (경로 입력)
          └─ StatementImportScreen
              ├─ on_mount → _extract_and_dedup (worker)
              │   ├─ adapter 가 rows : list[StatementRow] 추출
              │   └─ ledger 가져와 (matched, prev_imported, proposals) 분류
              ├─ Ctrl+Enter → action_confirm
              │   └─ proposals 의 각 row 에 create_entry 호출
              └─ ESC 닫기
```

## 단계별 흐름

1. **파일 선택** — 메뉴 진입 → 경로 prompt.
2. **자동 어댑터 선택** — 파일 확장자 + sniff:
   - CSV: `core/csv_adapters/` (현대카드, 신한카드, …).
   - HTML: `core/html_adapters/` (이메일 명세서 등).
   - PDF: `core/pdf_adapters/`.
3. **추출 + dedup** — adapter 결과를 후잉 ledger 와 비교:
   - **matched** — 이미 ledger 에 같은 거래 있음 (skip).
   - **prev_imported** — `import_log` 테이블에 기록된 import 흔적 있음.
   - **proposals** — 신규 (입력 후보).
4. **사용자 확인 + Ctrl+Enter** — 신규만 일괄 후잉에 create. 성공한
   row 는 `import_log` 에 기록 → 다음 import 시 prev_imported 로 인식.
5. **에러 안내** — adapter 가 빈 결과를 돌려주거나 날짜 파싱 실패면
   status 메시지 (CL #52832+ 의 strptime hardening — 시나리오 11 의
   '크래시 보강' 항목).

## 어댑터 추가하기

```
core/src/whooing_core/<csv|html|pdf>_adapters/
  └─ <카드사>_<형식>.py
      ├─ class <Vendor>Adapter:
      │   def can_handle(path, head_bytes) -> bool
      │   def extract(path) -> list[StatementRow]
      └─ 등록은 __init__.py 의 ADAPTERS 리스트 끝에 append.
```

`StatementRow` 는 `date(YYYYMMDD)`, `merchant`, `amount` (int, KRW),
`raw_line` 을 가진다 — `core/csv_adapters/__init__.py`.

## 에지 케이스

- **신규 어댑터가 잘못된 date 형식 (YYYY-MM-DD 등) 반환** — 본 화면이
  `len(date)==8 and isdigit` 으로 필터 → 어댑터 수정 안내 status.
- **금액 음수 (환불)** — proposal 그대로 — 후잉이 음수 money 수용.
  중복 평가 시 부호 반대 매칭 휴리스틱 활용 (시나리오 05).
- **같은 명세서 두 번 import** — `import_log` 가 prev_imported 로 모두
  표시 → 신규 0건.

## 관련 코드

- [`screens/statement_import.py`](../../tui/src/whooing_tui/screens/statement_import.py)
  — 모달 + worker + dedup 로직.
- [`core/csv_adapters/`](../../core/src/whooing_core/csv_adapters/) —
  CSV 어댑터들. `__init__.py` 의 `pick_adapter()` 가 선택 진입점.
- [`core/html_adapters/`](../../core/src/whooing_core/html_adapters/).
- [`core/pdf_adapters/`](../../core/src/whooing_core/pdf_adapters/).
- [`core/db.py`](../../core/src/whooing_core/db.py) — `import_log`
  테이블 스키마 (CL #51129+).
