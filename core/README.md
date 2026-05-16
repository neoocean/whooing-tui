# whooing-core

후잉 가계부([whooing.com](https://whooing.com)) 데이터를 다루는 두 시스템 —
**[whooing-mcp-server-wrapper](https://github.com/neoocean/whooing-mcp-server-wrapper)**
(MCP 도구 묶음) 과 **whooing-tui** (터미널 UI) — 가 공유하는 코어 라이브러리.

## 책임 범위

```mermaid
flowchart TB
    subgraph core["whooing-core (본 패키지)"]
        A["html_adapters/<br/>(하나/현대카드 보안메일 복호화 + 파싱)"]
        B["pdf_adapters/<br/>(신한/현대카드 PDF 명세서)"]
        C["csv_adapters/<br/>(신한/국민/현대/삼성카드 CSV)"]
        D["attachments.py<br/>(sha256 dedup 파일 storage)"]
        E["db/<br/>(SQLite schema + annotations / hashtags / entry_attachments)"]
    end

    Wrapper["whooing-mcp-server-wrapper<br/>(read-only on db/attachments)"] -->|import| core
    TUI["whooing-tui<br/>(write owner — db/attachments)"] -->|import| core
```

## 책임이 **아닌** 것

- HTTP / API 호출 (각 consumer 가 알아서)
- MCP 서버 등록 / TUI 화면 (해당 repo 의 책임)
- P4 자동 sync (wrapper 의 자체 정책 — core 외부)
- pending queue 테이블 (wrapper 단독 사용)

## 설치

```bash
# 두 consumer 의 pyproject.toml 에 dependency 로 등록:
dependencies = [
    "whooing-core @ git+ssh://p4d.tail94e991.ts.net/whooing-core.git@v0.1.0",
    # 또는 PyPI publish 후:
    # "whooing-core>=0.1",
]
```

Playwright 브라우저는 한 번만 설치 필요 (HTML 보안메일 복호화):

```bash
playwright install chromium
```

## 사용 예

```python
# HTML 카드 보안메일 (하나/현대) 복호화 + 거래 추출
from whooing_core.html_adapters import detect, parse, known_issuers

issuer, rows = await parse_async(
    "/path/to/hyundaicard_20260425.html",
    password="820115",  # 생년월일 6자리 (한국 카드사 모두 공통)
)
for row in rows:
    print(row.date, row.merchant, row.amount)
```

```python
# 첨부파일 (sha256 dedup)
from whooing_core.attachments import store_file
sha, rel_path, was_new = store_file(
    src="/Users/me/Downloads/invoice.pdf",
    attachments_root="/Users/me/.whooing/attachments",
)
```

```python
# DB schema init (TUI 가 owner)
from whooing_core.db import init_schema, current_version
conn = init_schema("/Users/me/.whooing/data.sqlite")
print(f"schema version: {current_version(conn)}")
```

## 문서

- [DESIGN.md](DESIGN.md) — schema, 어댑터 구조, 분리 정책
- [CHANGELOG.md](CHANGELOG.md) — 버전별 변경
- [mcp/](../mcp/) — read-only consumer (ex-whooing-mcp-server-wrapper, archived 2026-05-10 후 monorepo 로 흡수)
- [whooing-tui](../whooing-tui/) — write owner

## License

MIT.
