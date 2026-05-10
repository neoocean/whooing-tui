"""거래 항목 ↔ 첨부파일 storage layer (sha256 dedup).

저장 구조:
  <attachments_root>/YYYY/YYYY-MM-DD/<filename>

같은 SHA256 (= 같은 파일 내용) 이 이미 있으면 디스크에 재복사 안 함 (db row 만 추가).
db CRUD (entry_attachments 테이블) 는 `whooing_core.db` 에 분리.

본 모듈은 path/env 의존성 0 — `attachments_root` 는 항상 caller 가 인자로 전달.

CL #51141+ (A13) 휴지통: `delete_attachment` / `purge_attachments_for_entry`
가 `trash=True` 옵션. True 면 디스크에서 unlink 대신
`<attachments_root>/.trash/YYYYMMDD/<filename>` 로 mv. 30일 정도 보존 후 별도
cleanup 도구가 purge (TBD).
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from whooing_core.dates import KST

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _today_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_mime(path: Path) -> str | None:
    mt, _ = mimetypes.guess_type(str(path))
    return mt


def _move_to_trash(
    full_path: Path,
    *,
    attachments_root: Path,
) -> Path | None:
    """`<root>/.trash/YYYYMMDD/<filename>` 으로 mv. CL #51141+ (A13).

    같은 이름 이미 있으면 sha-suffix 같은 정책 — 단순화 위해 timestamp suffix.
    이동 실패 시 None.
    """
    if not full_path.exists():
        return None
    trash_root = Path(attachments_root) / ".trash" / datetime.now(KST).strftime("%Y%m%d")
    trash_root.mkdir(parents=True, exist_ok=True)
    target = trash_root / full_path.name
    if target.exists():
        ts = datetime.now(KST).strftime("%H%M%S%f")
        target = trash_root / f"{full_path.stem}-{ts}{full_path.suffix}"
    try:
        shutil.move(str(full_path), str(target))
        return target
    except OSError as e:
        log.warning("trash mv 실패 %s → %s: %s", full_path, target, e)
        return None


def purge_trash_older_than(
    attachments_root: str | Path,
    *,
    days: int = 30,
) -> dict[str, int]:
    """`<root>/.trash/YYYYMMDD/` 디렉터리 중 N일 초과된 것 unlink.

    Returns: `{'dirs_purged': N, 'files_purged': M}`. 실패는 silent log.
    """
    from datetime import timedelta
    root = Path(attachments_root).expanduser() / ".trash"
    if not root.is_dir():
        return {"dirs_purged": 0, "files_purged": 0}
    cutoff = datetime.now(KST) - timedelta(days=days)
    dirs_purged = 0
    files_purged = 0
    for child in root.iterdir():
        if not child.is_dir() or len(child.name) != 8 or not child.name.isdigit():
            continue
        try:
            d = datetime.strptime(child.name, "%Y%m%d").replace(tzinfo=KST)
        except ValueError:
            continue
        if d >= cutoff:
            continue
        try:
            for f in child.iterdir():
                f.unlink()
                files_purged += 1
            child.rmdir()
            dirs_purged += 1
        except OSError as e:
            log.warning("trash purge 실패 %s: %s", child, e)
    return {"dirs_purged": dirs_purged, "files_purged": files_purged}


def copy_to_attachments(
    src_path: str | Path,
    *,
    attachments_root: str | Path,
    attach_date: str | None = None,
) -> tuple[Path, str, int]:
    """src 를 attachments_root/YYYY/YYYY-MM-DD/<basename> 으로 복사.

    Args:
      src_path: 원본 파일 경로
      attachments_root: 모든 첨부의 루트 (예: ~/.whooing/attachments)
      attach_date: YYYY-MM-DD (default: today). 디렉터리 분류용.

    Returns:
      (copied_path: Path, sha256_hex: str, size_bytes: int)
      copied_path 는 절대 경로.

    Same sha256 이 같은 (date) 폴더에 이미 있으면 재복사 안 함 — 기존 파일 path 반환.
    같은 sha256 이 다른 폴더에 있으면 새 폴더에도 사본 작성 (단순화 — symlink/hardlink X).
    """
    src = Path(src_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"source file not found: {src}")
    if not src.is_file():
        raise ValueError(f"not a regular file: {src}")

    sha256 = _sha256_of_file(src)
    size = src.stat().st_size

    date_str = attach_date or _today_str()
    year = date_str[:4]
    target_dir = Path(attachments_root).expanduser() / year / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    target = target_dir / src.name
    # 충돌: 같은 이름이 이미 있고 같은 sha256 면 그대로 reuse.
    if target.exists():
        if _sha256_of_file(target) == sha256:
            log.info("attachment already exists at %s (same sha256), reusing", target)
            return target, sha256, size
        # 다른 내용이면 sha256 prefix 8글자 suffix 로 (CL #51140+ A14):
        #   x.pdf → x-ab12cd34.pdf
        # 종전엔 x-1.pdf → x-2.pdf ... 카운터 — 100개 중복 시 100번 hash.
        # sha 기반은 충돌 자체가 sha 동일을 의미 (이미 위 if 에서 처리).
        candidate = target_dir / f"{src.stem}-{sha256[:8]}{src.suffix}"
        if candidate.exists():
            if _sha256_of_file(candidate) == sha256:
                return candidate, sha256, size
            # sha 다른데 8자 prefix 도 충돌 — 매우 드문 케이스. 16자로.
            candidate = target_dir / f"{src.stem}-{sha256[:16]}{src.suffix}"
        target = candidate

    shutil.copy2(src, target)
    log.info("copied %s → %s (%d bytes, sha256 %s)", src, target, size, sha256[:12])
    return target, sha256, size


def upsert_attachment(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    section_id: str | None,
    file_path: str,            # relative path
    original_path: str | None,
    original_filename: str,
    file_size_bytes: int | None,
    file_sha256: str | None,
    mime_type: str | None,
    note: str | None,
) -> dict[str, Any]:
    """attach row 를 db 에 삽입 (또는 같은 entry+sha256 이면 기존 row 반환).

    CL #51148+ (A3): application-level CHECK — schema 의 NOT NULL 강제는
    SQLite ALTER 한계로 위험 (table rebuild 필요). 대신 caller 가 dedup /
    감사 / GC 모두 안정적으로 작동하기 위해 필수인 두 컬럼을 함수 레벨에서
    강제 검증. 종전 호출자 (`add_attachment`) 는 이미 모두 채우므로 무영향.
    """
    if not entry_id:
        raise ValueError("entry_id 는 필수")
    if not file_path:
        raise ValueError("file_path 는 필수")
    if not original_filename:
        raise ValueError("original_filename 는 필수")
    if file_size_bytes is None or file_size_bytes < 0:
        raise ValueError(
            f"file_size_bytes 는 음수 아닌 정수 필수 (got {file_size_bytes!r})"
        )
    if not file_sha256:
        raise ValueError(
            "file_sha256 는 필수 (dedup / GC / audit 가 사용)"
        )
    if file_sha256:
        existing = conn.execute(
            """SELECT * FROM entry_attachments
               WHERE entry_id = ? AND file_sha256 = ?
               LIMIT 1""",
            (entry_id, file_sha256),
        ).fetchone()
        if existing:
            return dict(existing)

    cur = conn.execute(
        """INSERT INTO entry_attachments
           (entry_id, section_id, file_path, original_path, original_filename,
            file_size_bytes, file_sha256, mime_type, note, attached_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entry_id, section_id, file_path, original_path, original_filename,
         file_size_bytes, file_sha256, mime_type, note, _now_iso()),
    )
    aid = cur.lastrowid
    row = conn.execute("SELECT * FROM entry_attachments WHERE id = ?", (aid,)).fetchone()
    return dict(row)


def update_attachment_note(
    conn: sqlite3.Connection,
    attachment_id: int,
    note: str | None,
) -> dict[str, Any] | None:
    """첨부의 note 만 갱신. CL #51143+ (A9).

    종전엔 `upsert_attachment` 의 첨부 시점에만 note 가 들어갔다 — 사후
    편집 API 부재. 본 함수가 단일 column UPDATE.

    빈 문자열 ("" 또는 whitespace-only) 은 None 으로 정규화 (db 의 NULL).
    매칭 row 없으면 None 반환.

    Returns: 갱신된 row dict (또는 None).
    """
    row = conn.execute(
        "SELECT * FROM entry_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if not row:
        return None
    normalized = (note or "").strip() or None
    conn.execute(
        "UPDATE entry_attachments SET note = ? WHERE id = ?",
        (normalized, attachment_id),
    )
    updated = conn.execute(
        "SELECT * FROM entry_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    return dict(updated) if updated else None


def list_attachments_for(
    conn: sqlite3.Connection,
    entry_ids: list[str],
    *,
    section_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """entry_id → list of attachment rows.

    CL #51144+ (A5): `section_id` 명시 시 그 섹션의 첨부만 — 후잉이 cross-
    section 으로 entry_id 를 재사용할 가능성에 대한 방어. None 이면 종전
    동작 (모든 섹션 합).
    """
    if not entry_ids:
        return {}
    placeholders = ",".join("?" * len(entry_ids))
    if section_id is None:
        rows = conn.execute(
            f"""SELECT * FROM entry_attachments
                WHERE entry_id IN ({placeholders})
                ORDER BY entry_id, attached_at""",
            entry_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT * FROM entry_attachments
                WHERE entry_id IN ({placeholders})
                  AND (section_id = ? OR section_id IS NULL)
                ORDER BY entry_id, attached_at""",
            [*entry_ids, section_id],
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        out.setdefault(d["entry_id"], []).append(d)
    return out


def purge_attachments_for_entry(
    conn: sqlite3.Connection,
    entry_id: str,
    *,
    attachments_root: str | Path,
    delete_files: bool = True,
) -> list[dict[str, Any]]:
    """거래 삭제 시 호출 — 해당 entry_id 의 모든 첨부 row + (옵션) 디스크 파일.

    CL #51132+ (A1): 종전엔 `entry_attachments` 가 FK 없이 entry_id 만 보관 →
    거래 삭제 시 row + 디스크 파일이 orphan 으로 남음. 본 함수가 의미적 강제.

    각 row 마다 `delete_attachment` 와 동일한 dedup 정책 (같은 sha 가 다른
    entry 에 살아있으면 디스크 보존). 반환 dict 의 `file_deleted` /
    `file_kept_other_refs` 필드로 호출자가 P4 submit paths 계산 가능.
    """
    rows = conn.execute(
        "SELECT * FROM entry_attachments WHERE entry_id = ?", (entry_id,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        info = dict(r)
        conn.execute("DELETE FROM entry_attachments WHERE id = ?", (info["id"],))
        if delete_files:
            sha = info.get("file_sha256")
            other = conn.execute(
                "SELECT COUNT(*) FROM entry_attachments WHERE file_sha256 = ?",
                (sha,),
            ).fetchone()[0] if sha else 0
            if other == 0:
                full_path = Path(attachments_root).expanduser() / info["file_path"]
                try:
                    if full_path.exists():
                        full_path.unlink()
                        info["file_deleted"] = True
                except OSError as e:
                    info["file_delete_error"] = str(e)
            else:
                info["file_kept_other_refs"] = other
        out.append(info)
    return out


def find_orphan_attachments(
    conn: sqlite3.Connection,
    valid_entry_ids: set[str],
) -> list[dict[str, Any]]:
    """현재 후잉 ledger 에 없는 entry_id 를 가진 첨부 row 들.

    CL #51132+ (A2). 외부 caller (TUI / CLI) 가 후잉 entries 를 fetch 한 뒤
    그 set 을 넘겨 — 본 함수는 단순 set difference. 데이터 비파괴.
    """
    if not valid_entry_ids:
        # 후잉이 비었으면 모든 첨부가 orphan — 안전성 위해 빈 list 반환.
        # caller 가 명시적으로 valid set 을 가지고 있을 때만 의미 있음.
        return []
    rows = conn.execute(
        "SELECT * FROM entry_attachments ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows if r["entry_id"] not in valid_entry_ids]


def cleanup_orphan_attachments(
    conn: sqlite3.Connection,
    valid_entry_ids: set[str],
    *,
    attachments_root: str | Path,
    delete_files: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """orphan 청소 — `find_orphan_attachments` 결과를 (옵션) 실제 삭제.

    CL #51132+ (A2). dry_run=True 면 후보만 반환 (write X). 호출자는 보통
    dry_run 으로 미리 확인 후 사용자 확정 시 dry_run=False 로 재호출.

    Returns:
      {
        'orphan_count': N,
        'rows_deleted': N (dry_run=False 일 때만),
        'files_deleted': N (dedup 으로 보존된 것 제외),
        'files_kept_dedup': N,
        'orphans': [row dict, ...]
      }
    """
    orphans = find_orphan_attachments(conn, valid_entry_ids)
    out: dict[str, Any] = {
        "orphan_count": len(orphans),
        "rows_deleted": 0,
        "files_deleted": 0,
        "files_kept_dedup": 0,
        "orphans": orphans,
    }
    if dry_run or not orphans:
        return out
    for info in orphans:
        conn.execute("DELETE FROM entry_attachments WHERE id = ?", (info["id"],))
        out["rows_deleted"] += 1
        if not delete_files:
            continue
        sha = info.get("file_sha256")
        other = conn.execute(
            "SELECT COUNT(*) FROM entry_attachments WHERE file_sha256 = ?",
            (sha,),
        ).fetchone()[0] if sha else 0
        if other > 0:
            out["files_kept_dedup"] += 1
            continue
        full_path = Path(attachments_root).expanduser() / info["file_path"]
        try:
            if full_path.exists():
                full_path.unlink()
                out["files_deleted"] += 1
        except OSError:
            log.warning("orphan file unlink 실패: %s", full_path)
    return out


def delete_attachment(
    conn: sqlite3.Connection,
    attachment_id: int,
    *,
    attachments_root: str | Path,
    delete_file: bool = True,
    trash: bool = False,
) -> dict[str, Any] | None:
    """row 제거 + (옵션) 디스크 파일도 제거 / 휴지통 이동.

    같은 sha256 의 다른 row 가 남아있으면 파일은 보존 (다른 entry 가 참조).

    CL #51141+ (A13): `trash=True` 면 디스크 unlink 대신 `.trash/YYYYMMDD/`
    로 mv. info 에 `file_trashed=True` + `trash_path=<path>` 보존.
    """
    row = conn.execute(
        "SELECT * FROM entry_attachments WHERE id = ?", (attachment_id,)
    ).fetchone()
    if not row:
        return None
    info = dict(row)
    conn.execute("DELETE FROM entry_attachments WHERE id = ?", (attachment_id,))

    if delete_file:
        sha = info.get("file_sha256")
        other = conn.execute(
            "SELECT COUNT(*) FROM entry_attachments WHERE file_sha256 = ?",
            (sha,),
        ).fetchone()[0] if sha else 0
        if other == 0:
            full_path = Path(attachments_root).expanduser() / info["file_path"]
            if trash:
                trashed = _move_to_trash(
                    full_path, attachments_root=Path(attachments_root).expanduser(),
                )
                if trashed is not None:
                    info["file_trashed"] = True
                    info["trash_path"] = str(trashed)
                elif full_path.exists():
                    info["file_delete_error"] = "trash mv 실패"
            else:
                try:
                    if full_path.exists():
                        full_path.unlink()
                        info["file_deleted"] = True
                except OSError as e:
                    info["file_delete_error"] = str(e)
        else:
            info["file_kept_other_refs"] = other
    return info
