# 03. 영수증 · 인보이스 첨부

## 목적

거래 한 건에 PDF 영수증 / 카드사 명세서 / 이미지 등 파일을 묶어 보관.
저장은 로컬 (`<project>/attachment/YYYY/YYYY-MM-DD/<filename>`) +
P4. 후잉 본체엔 첨부 개념이 없어 *순수 로컬* 기능.

## 주요 경로

```
EntriesScreen
  └─ f (또는 메뉴 → 선택 거래 첨부)
      └─ AttachmentBrowser (모달)
          ├─ a : 파일 추가  → FilePicker → copy_to_attachments
          ├─ Enter : 미리보기 (텍스트 / PDF)
          └─ Del / d : 첨부 삭제

EntriesScreen
  └─ 메뉴 → PDF 영수증/인보이스 첨부…
      └─ ReceiptAttachScreen (자동 추출 + 거래 매칭)
```

## 단계별 흐름

### 단일 거래에 파일 추가 (`f`)

1. 거래 cursor 에서 `f` → `AttachmentBrowser` 모달.
2. `a` 키 → `FilePicker` 모달 → 디스크 경로 선택.
3. `copy_to_attachments(src, root, attach_date)` 가:
   - 대상 디렉토리: `attachment/YYYY/YYYY-MM-DD/`.
   - sha256 으로 dedup — 동일 파일이 이미 있으면 기존 사용.
   - sqlite `entry_attachments` 에 row insert (entry_id ↔ file_path).
4. 자동 P4 submit (db + attachment 파일 함께, 한 CL).

### 미리보기 (`Enter`)

1. 첨부 row 에서 Enter → `core/preview.py` 의 `extract_preview()`.
2. 텍스트 파일: 그대로 표시. PDF: pdfminer / pdfplumber 로 텍스트 추출.
3. 이미지 등 지원 불가: "미리보기 불가 (파일 타입)" 안내.

### 영수증 wizard (메뉴 → PDF 영수증)

1. 파일 경로 prompt → `ReceiptAttachScreen`.
2. PDF 자동 추출 (`core/receipt/`) — 날짜 / 가맹점 / 금액.
3. 추출된 메타로 후잉 ledger 와 매칭:
   - 같은 날 ± 2일 windows 안의 거래에서 금액 일치 거래 후보.
   - 사용자가 선택해 *기존* 거래에 첨부 또는 *새* 거래 생성 후 첨부.

## 저장 정책 (보안)

- **P4 에는 올라간다** — 본인 host 내부 데이터.
- **GitHub 미러에는 절대 안 올라간다** — `/attachment/` 전체가
  `.gitignore` 차단 (카드사 명세서 PDF 같은 민감 PII 보호).
- 자세한 동기화 규칙: [`MEMORY.md §5`](../../tui/MEMORY.md).

## 에지 케이스

- **sha256 동일한 파일 재첨부** — 같은 거래에 중복 row 만들지 않음
  (UNIQUE 제약). 다른 거래에 같은 파일 첨부는 허용 — file_path 는
  공유, attachment row 만 별도.
- **거래 삭제 시 첨부** — `_purge_local()` 가 sqlite row 정리 +
  디스크 파일도 *고아* 면 삭제 (해당 sha256 을 참조하는 row 가 0개).
- **P4 submit 실패** — UI 영향 없음. 로컬엔 정상 저장됨. 다음 mutation
  또는 종료 시 `flush_on_exit` 가 재시도 (시나리오 09).

## 관련 코드

- [`screens/attachment_browser.py`](../../tui/src/whooing_tui/screens/attachment_browser.py)
  — 모달 본체 + 키 핸들러.
- [`screens/file_picker.py`](../../tui/src/whooing_tui/screens/file_picker.py)
  — 파일 경로 picker.
- [`screens/receipt_attach.py`](../../tui/src/whooing_tui/screens/receipt_attach.py)
  — 영수증 자동 매칭 wizard.
- [`core/attachments.py`](../../core/src/whooing_core/attachments.py)
  — `copy_to_attachments`, `upsert_attachment`.
- [`core/preview.py`](../../core/src/whooing_core/preview.py)
  — 텍스트/PDF preview 추출.
- [`core/receipt/`](../../core/src/whooing_core/receipt/)
  — 영수증 메타 추출 어댑터.
