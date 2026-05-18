# whooing-core — Design

## §1. 목적

후잉 가계부의 외부 입력 (카드사 명세서) 을 정형화 + 첨부 + annotation 데이터를
SQLite 에 보관하는 **공통 어댑터/스토리지 레이어**. 두 consumer
(`whooing-mcp-server-wrapper`, `whooing-tui`) 가 동일 코드를 import 해 사용.

## §2. 모듈 경계

| 모듈 | 책임 | 외부 의존 |
|---|---|---|
| `html_adapters/` | 카드 보안메일 .html → CSVRow list (Playwright 복호화 후 DOM 파싱) | playwright, beautifulsoup4 |
| `pdf_adapters/` | 카드 PDF 명세서 → CSVRow list | pdfplumber |
| `csv_adapters/` | 카드 CSV → CSVRow list | (stdlib csv) |
| `attachments.py` | 파일 sha256 dedup storage + trash + GC | (stdlib hashlib, shutil) |
| `db.py` | SQLite 스키마 v8 + 마이그레이션 + annotations/hashtags/tag_meta/entry_attachments/audit_log/entries_cache CRUD | (stdlib sqlite3) |
| **`entries_cache.py`** | **(CL #52758+, schema v8)** 후잉 거래내역 영구 캐시 layer — upsert / list / oldest_date / purge | (stdlib sqlite3, json) |
| **`preview.py`** | **(CL #52750+)** 첨부 파일 미리보기 텍스트 추출 — text/* (UTF-8/cp949/latin-1 fallback) + application/pdf (per-page) | pdfplumber (재사용) |
| `receipt/extractor.py` | PDF 영수증 regex 추출 (date / amount / merchant) | pdfplumber (재사용) |

**core 가 수행하지 않는 것:**
- HTTP / 후잉 API 호출 → consumer 책임
- pending queue (wrapper 단독) → wrapper 잔류
- P4 자동 sync → wrapper 의 정책
- MCP / TUI 위젯 → 각 consumer

## §3. 데이터 모델 (CSVRow)

모든 adapter 가 동일한 dataclass 를 반환:

```python
@dataclass
class CSVRow:
    date: str          # YYYYMMDD
    amount: int        # 음수 = 환불/할인
    merchant: str
    raw: dict          # adapter 별 메타 (cells, fee 등)
```

## §4. SQLite 스키마

소유 정책 — **whooing-tui 가 owner**, wrapper 는 read-only.
schema migration 책임도 TUI. wrapper 는 `PRAGMA user_version` 만 확인.

WAL 모드 + `busy_timeout=5000` — TUI 가 init 시 설정. wrapper 가 동시 SELECT
해도 락 충돌 없음.

현재 `SCHEMA_VERSION = 8` (CL #52758+).

| 테이블 | 컬럼 (요약) | 도입 / 소유 |
|---|---|---|
| `entry_annotations` | entry_id PK, memo, section_id, created/updated_at | v1 / core |
| `entry_hashtags` | entry_id, tag (composite PK), section_id (v7), created_at | v1 + v7 (section) |
| `entry_attachments` | id PK, entry_id, sha256, file_path, mime, original_filename, note, section_id (v6+), ... | v2+ |
| `statement_import_log` | id PK, source_file, source_kind, issuer, ... entry_date, merchant, amount, status, section_id | v3+ |
| `tag_meta` | tag, section_id (PK), color, updated_at | v7+ |
| `attachment_audit_log` | id PK, attachment_id, entry_id, action, ts, details_json | v7+ |
| **`entries_cache`** | (section_id, entry_id) PK, entry_date, l_account_id, r_account_id, money, item, memo, raw_json, fetched_at | **v8 (CL #52758+)** — 사용자 요청 (점진적 필터 확장 + 캐시) |
| `schema_meta` | key/value (`version` 등) | v1 |

## §5. 어댑터 등록 패턴

`html_adapters/__init__.py` (csv/pdf 도 동일):

```python
_REGISTRY: list[tuple[str, IsMatchFn, ParseFn]] = [
    ("hanacard_secure_mail", hanacard.is_match, hanacard.parse_html),
    ("hyundaicard_secure_mail", hyundai.is_match, hyundai.parse_html),
]

def detect(html_path) -> HTMLDetectResult: ...
def parse(html_path, password, issuer="auto") -> tuple[str, list[CSVRow]]: ...
def known_issuers() -> list[str]: ...
```

새 카드사 어댑터 추가 = 모듈 1개 + registry 1줄.

## §6. 파일 storage (attachments.py)

```
$WHOOING_DATA_DIR/attachments/
  └─ <sha256[:2]>/<sha256>.<ext>
```

- sha256 의 첫 2자리로 1-deep sharding (몇만 파일까지 ls 빠름)
- dedup: 같은 sha256 이면 기존 파일 재사용, entry_attachments row 만 추가
- store_file() 반환: `(sha256, rel_path, was_new: bool)`

## §7. Consumer 와의 계약

### whooing-tui (write owner)
- `init_schema(db_path)` 호출 — schema 생성 / migrate
- WAL 모드 + busy_timeout 설정
- annotations / hashtags / entry_attachments / statement_import_log 모두 write
- attachments dir 모두 write (store_file)

### whooing-mcp-server-wrapper (read-only)
- `current_version(db_path)` 만 호출 — mismatch 면 error 메시지 "TUI 를 먼저 실행하세요"
- annotations / hashtags / entry_attachments — `PRAGMA query_only=ON` 또는 `mode=ro` URI 로 SELECT 만
- statement_import_log — TUI 가 채움, wrapper 는 audit 시 SELECT 만
- attachments dir — read-only (파일 열기만 — 추가/삭제 X)

## §8. 패스워드 정책

한국 카드사 (하나/현대/...) 모두 사용자 생년월일 6자리 (YYMMDD) 를 보안메일
패스워드로 사용 → 단일 env 키 (`WHOOING_CARD_HTML_PASSWORD`) 공유.
core 는 env 를 읽지 않음 — consumer 가 읽어 인자로 전달.

## §9. 향후

- 추가 카드사 (삼성/국민/우리/신한) HTML adapter
- OCR PDF adapter (이미지 PDF — 현재 텍스트 추출 가능 PDF 만)
- entry_attachments 에 첨부파일 미리보기 thumbnail 캐시
