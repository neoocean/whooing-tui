# Changelog

[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식.

## v0.1.0 — 2026-05-10

Extracted from `whooing-mcp-server-wrapper` v0.1.12 (2026-05-10) per architectural
split decision (TUI 가 user-facing flow 가져감, wrapper 는 LLM 자동화 영역에 집중).

### Added

- `whooing_core/html_adapters/` — base + hanacard_secure_mail + hyundaicard_secure_mail.
  - `decrypt_html_with_playwright()` (CryptoJS / vestmail 양식 지원).
  - `detect()` 가 1MB head scan 으로 issuer 판정.
- `whooing_core/csv_adapters/` — shinhan / kookmin / hyundai_card / samsung_card.
- `whooing_core/pdf_adapters/` — shinhan / hyundai_card.
- `whooing_core/attachments.py` — sha256 dedup storage (`store_file`, `delete_file`).
- `whooing_core/db/` — SQLite schema (annotations / hashtags / entry_attachments /
  statement_import_log) + migration. WAL 모드 + busy_timeout 설정 helper.

### Notes

- pending queue 는 wrapper 잔류 (single consumer 라 core 분리 의미 X).
- p4_sync 는 wrapper 잔류 (TUI 는 P4 안 씀).
- consumer 가 `WHOOING_DATA_DIR` 등 env 를 읽음 — core 는 path/password 모두
  인자로 받음 (env 의존성 0).
