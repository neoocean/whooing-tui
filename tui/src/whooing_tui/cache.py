"""inter-session 캐시 — sqlite 기반 accounts/entries 보관.

목적: 후잉 분당 20 throttle 부담을 줄이고 startup speed 를 높인다 (캐시
hit 면 0.01초, miss 면 httpx 1-3초). mcp-server-wrapper 의
`whooing-data.sqlite` 와는 분리된 별도 db 다 (.gitignore `*.sqlite` 가
양쪽 차단).

스키마 (단순 KV):
  account_cache(section_id PK, raw_json, fetched_at_unix)
  entries_cache(section_id, start_date, end_date, raw_json, fetched_at_unix,
                PRIMARY KEY (section_id, start_date, end_date))

TTL:
  accounts  : 1시간 (계정 재구성은 드물다)
  entries   : 5분 (mutation 가능성, 짧게)
  사용자가 'r' refresh 누르면 직접 invalidate.

Cache hit / miss / invalidate 모두 동기 호출. 화면이 already async 워커
안에서 호출하므로 sqlite 의 동기 IO 가 UI 를 막지 않는다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_cache (
    section_id      TEXT PRIMARY KEY,
    raw_json        TEXT NOT NULL,
    fetched_at_unix INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entries_cache (
    section_id      TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    raw_json        TEXT NOT NULL,
    fetched_at_unix INTEGER NOT NULL,
    PRIMARY KEY (section_id, start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_entries_section ON entries_cache(section_id);
"""

DEFAULT_ACCOUNTS_TTL_SEC = 3600
DEFAULT_ENTRIES_TTL_SEC = 300


class CacheStore:
    """sqlite-backed inter-session cache. file path 지정해 인스턴스화.

    `:memory:` 도 지원 (테스트용). 디스크 path 면 부모 디렉토리 자동 생성.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # 스레드 안전성: Textual 의 단일 이벤트 루프 + 워커가 한 번에 1개
        # 호출하므로 단일 connection 으로 충분. check_same_thread=False 는
        # async worker thread 에서도 같은 connection 을 쓰기 위함.
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, isolation_level=None,
        )
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ---- accounts ------------------------------------------------------

    def get_accounts(
        self, section_id: str, *, max_age_sec: int = DEFAULT_ACCOUNTS_TTL_SEC,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT raw_json, fetched_at_unix FROM account_cache WHERE section_id = ?",
            (section_id,),
        ).fetchone()
        if row is None:
            return None
        raw_json, fetched_at = row
        if max_age_sec >= 0 and (int(time.time()) - int(fetched_at)) >= max_age_sec:
            return None
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            log.warning("account_cache row 의 raw_json 디코드 실패 — invalidate")
            self.invalidate_accounts(section_id)
            return None

    def put_accounts(self, section_id: str, raw: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO account_cache(section_id, raw_json, fetched_at_unix) "
            "VALUES (?, ?, ?)",
            (section_id, json.dumps(raw, ensure_ascii=False), int(time.time())),
        )

    def invalidate_accounts(self, section_id: str | None = None) -> None:
        if section_id is None:
            self._conn.execute("DELETE FROM account_cache")
        else:
            self._conn.execute(
                "DELETE FROM account_cache WHERE section_id = ?", (section_id,),
            )

    # ---- entries -------------------------------------------------------

    def get_entries(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
        *,
        max_age_sec: int = DEFAULT_ENTRIES_TTL_SEC,
    ) -> list[dict[str, Any]] | None:
        row = self._conn.execute(
            "SELECT raw_json, fetched_at_unix FROM entries_cache "
            "WHERE section_id = ? AND start_date = ? AND end_date = ?",
            (section_id, start_date, end_date),
        ).fetchone()
        if row is None:
            return None
        raw_json, fetched_at = row
        if max_age_sec >= 0 and (int(time.time()) - int(fetched_at)) >= max_age_sec:
            return None
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            log.warning("entries_cache row 디코드 실패 — invalidate")
            self.invalidate_entries(section_id)
            return None
        return data if isinstance(data, list) else None

    def put_entries(
        self,
        section_id: str,
        start_date: str,
        end_date: str,
        entries: list[dict[str, Any]],
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO entries_cache"
            "(section_id, start_date, end_date, raw_json, fetched_at_unix) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                section_id, start_date, end_date,
                json.dumps(entries, ensure_ascii=False),
                int(time.time()),
            ),
        )

    def invalidate_entries(self, section_id: str | None = None) -> None:
        """mutation 발생 시 호출 — 해당 섹션의 모든 (start, end) 윈도우 캐시 제거."""
        if section_id is None:
            self._conn.execute("DELETE FROM entries_cache")
        else:
            self._conn.execute(
                "DELETE FROM entries_cache WHERE section_id = ?", (section_id,),
            )

    # ---- 디버깅 / 운영 -------------------------------------------------

    def stats(self) -> dict[str, int]:
        a = self._conn.execute("SELECT COUNT(*) FROM account_cache").fetchone()[0]
        e = self._conn.execute("SELECT COUNT(*) FROM entries_cache").fetchone()[0]
        return {"account_rows": int(a), "entries_rows": int(e)}


def default_cache_path(project_root: Path) -> Path:
    """기본 캐시 위치. .gitignore 의 .whooing-tui-cache/ 와 *.sqlite 가
    양쪽 차단하므로 GitHub 미러에 절대 새지 않는다."""
    return project_root / ".whooing-tui-cache" / "whooing-tui.sqlite"
