"""헤드리스 CLI — `python -m whooing_tui [subcommand]`.

서브커맨드 없이 실행하면 Textual TUI 가 열리고, 다음 서브커맨드는 GUI 없이
바로 실행된다 (cron / 스크립트 친화).

  sections list                — 섹션(가계부) 목록
  accounts list [--section ID] — 활성 섹션의 계정과목
  entries list  [--section ID] [--days N | --start YYYYMMDD --end YYYYMMDD]
                              — 거래내역 (기본: 최근 30일)

종료 코드:
  0  성공
  2  사용자 입력 오류 (USER_INPUT)
  3  자격증명 (AUTH)
  4  rate limit (분당/일일)
  5  upstream / 기타 후잉 응답 오류
  6  내부 버그 (INTERNAL)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from whooing_tui.auth import load_auth_from_env
from whooing_tui.client import WhooingClient
from whooing_tui.config import load_config
from whooing_tui.dates import days_ago_yyyymmdd, parse_yyyymmdd, today_yyyymmdd
from whooing_tui.errors import sanitize_for_log
from whooing_tui.models import ToolError
from whooing_tui.state import SessionState, default_section_id_from_env

log = logging.getLogger("whooing_tui")


# ---- 종료 코드 매핑 -----------------------------------------------------

_EXIT_BY_KIND = {
    "USER_INPUT": 2,
    "AUTH": 3,
    "RATE_LIMIT": 4,
    "UPSTREAM": 5,
    "INTERNAL": 6,
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whooing-tui",
        description=(
            "Textual TUI for Whooing 가계부. 서브커맨드 없이 실행하면 GUI 가 "
            "열리고, sections/accounts/entries 같은 서브커맨드는 헤드리스로 "
            "바로 실행된다."
        ),
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="-v: INFO 로그, -vv: DEBUG 로그.",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="결과를 JSON 으로 출력 (기본은 사람이 읽기 좋은 표).",
    )

    sub = p.add_subparsers(dest="command")

    # sections
    sec = sub.add_parser("sections", help="섹션(가계부) 작업.")
    sec_sub = sec.add_subparsers(dest="sections_command", required=True)
    sec_sub.add_parser("list", help="섹션 목록.")

    # accounts
    acc = sub.add_parser("accounts", help="계정과목 작업.")
    acc.add_argument(
        "--section", dest="section_id", default=None,
        help="섹션 ID (예: s133178). 미지정 시 .env 또는 첫 섹션.",
    )
    acc_sub = acc.add_subparsers(dest="accounts_command", required=True)
    acc_sub.add_parser("list", help="활성 섹션의 계정과목 목록.")

    # entries
    en = sub.add_parser("entries", help="거래내역 작업.")
    en.add_argument(
        "--section", dest="section_id", default=None,
        help="섹션 ID. 미지정 시 .env 또는 첫 섹션.",
    )
    en_sub = en.add_subparsers(dest="entries_command", required=True)
    en_list = en_sub.add_parser("list", help="거래내역 조회.")
    en_list.add_argument(
        "--days", type=int, default=None,
        help="최근 N일 (기본: config.entries.default_window_days = 30).",
    )
    en_list.add_argument(
        "--start", dest="start_date", default=None,
        help="시작일 YYYYMMDD. --end 와 함께 사용. 지정 시 --days 무시.",
    )
    en_list.add_argument(
        "--end", dest="end_date", default=None,
        help="종료일 YYYYMMDD. --start 와 함께 사용.",
    )

    # CL #51132+ (A2) — 첨부 orphan 정리.
    gc = sub.add_parser(
        "gc-attachments",
        help="후잉에 없는 entry_id 의 첨부 row + 디스크 파일 정리.",
    )
    gc.add_argument(
        "--section", dest="section_id", default=None,
        help="섹션 ID. 미지정 시 .env 또는 첫 섹션.",
    )
    gc.add_argument(
        "--days", type=int, default=365,
        help="후잉에서 fetch 할 entries 윈도우 (기본 365). orphan 판정의 valid set.",
    )
    gc.add_argument(
        "--dry-run", action="store_true",
        help="실제 삭제하지 않고 후보만 표시.",
    )
    gc.add_argument(
        "--keep-files", action="store_true",
        help="db row 만 삭제하고 디스크 파일은 보존.",
    )

    # CL #51146+ (A17) — 첨부파일 export.
    ex = sub.add_parser(
        "export-attachments",
        help="첨부파일을 zip 으로 export (entry 별 또는 section 별).",
    )
    ex.add_argument(
        "--entry", dest="entry_id", default=None,
        help="단일 entry_id 의 첨부만.",
    )
    ex.add_argument(
        "--section", dest="section_id", default=None,
        help="섹션 ID 의 모든 첨부 (--entry 와 둘 중 하나만).",
    )
    ex.add_argument(
        "--out", dest="out_path", required=True,
        help="출력 zip 파일 경로.",
    )

    return p


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---- 활성 섹션 결정 ------------------------------------------------------

async def _resolve_section_id(
    client: WhooingClient,
    explicit: str | None,
) -> tuple[str, str | None]:
    """우선순위: --section > WHOOING_SECTION_ID > sections-list 의 첫 항목.

    Returns (section_id, title-or-None).
    """
    if explicit:
        # 입력 검증은 후잉 응답 시 자연히 처리되므로 여기서는 패스
        return explicit, None
    env_id = default_section_id_from_env()
    if env_id:
        return env_id, None
    sections = await client.list_sections()
    if not sections:
        raise ToolError(
            "USER_INPUT",
            "후잉 계정에 섹션(가계부) 이 없습니다. whooing.com 에서 먼저 생성하세요.",
        )
    s = sections[0]
    sid = str(s.get("section_id") or s.get("id"))
    return sid, (s.get("title") or None)


# ---- 출력 헬퍼 ----------------------------------------------------------

def _print_table(rows: list[list[str]], headers: list[str]) -> None:
    """간단한 정렬 표 출력. 빈 리스트면 '(empty)' 만 한 줄."""
    if not rows:
        print("(empty)")
        return
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def _print_json(data: Any) -> None:
    """JSON 출력. 시크릿 필드는 sanitize 후 직렬화."""
    print(json.dumps(sanitize_for_log(data), ensure_ascii=False, indent=2))


# ---- 명령 처리 ----------------------------------------------------------

async def _cmd_sections_list(args: argparse.Namespace) -> int:
    auth = load_auth_from_env()
    client = WhooingClient(auth)
    sections = await client.list_sections()
    if args.as_json:
        _print_json(sections)
        return 0
    rows = [
        [str(s.get("section_id") or s.get("id") or ""), s.get("title") or ""]
        for s in sections
    ]
    _print_table(rows, headers=["section_id", "title"])
    return 0


async def _cmd_accounts_list(args: argparse.Namespace) -> int:
    auth = load_auth_from_env()
    client = WhooingClient(auth)
    section_id, _ = await _resolve_section_id(client, args.section_id)
    raw = await client.list_accounts(section_id)
    flat = WhooingClient.flatten_accounts(raw)
    state = SessionState()
    state.set_section(section_id)
    state.set_accounts(raw, flat)
    if args.as_json:
        _print_json({"section_id": section_id, "accounts": flat})
        return 0
    rows = [[a["type"], a["account_id"], a["title"]] for a in flat]
    _print_table(rows, headers=["type", "account_id", "title"])
    return 0


async def _cmd_entries_list(args: argparse.Namespace) -> int:
    cfg = load_config()
    auth = load_auth_from_env()
    client = WhooingClient(auth)
    section_id, _ = await _resolve_section_id(client, args.section_id)

    # 날짜 범위 결정
    if args.start_date or args.end_date:
        if not (args.start_date and args.end_date):
            raise ToolError("USER_INPUT", "--start 와 --end 는 함께 사용해야 합니다.")
        start = parse_yyyymmdd(args.start_date)
        end = parse_yyyymmdd(args.end_date)
    else:
        days = args.days if args.days is not None else cfg.default_window_days
        if days < 0:
            raise ToolError("USER_INPUT", f"--days 는 0 이상: got {days}")
        start = days_ago_yyyymmdd(days)
        end = today_yyyymmdd()

    entries = await client.list_entries(section_id, start, end)
    if args.as_json:
        _print_json({
            "section_id": section_id,
            "start_date": start, "end_date": end,
            "entries": entries,
        })
        return 0
    rows = [
        [
            e.get("entry_date") or "",
            str(e.get("money") or ""),
            e.get("l_account_id") or "",
            e.get("r_account_id") or "",
            e.get("item") or "",
        ]
        for e in entries
    ]
    _print_table(rows, headers=["date", "money", "left", "right", "item"])
    print(f"\n총 {len(entries)}건  ({start} ~ {end}, section={section_id})")
    return 0


# ---- entry point --------------------------------------------------------

async def _cmd_gc_attachments(args: argparse.Namespace) -> int:
    """CL #51132+ (A2) — 후잉 ledger 에 없는 첨부 row + 디스크 파일 청소.

    절차:
      1. 후잉 entries (지정 섹션, 최근 N 일) fetch → valid_entry_ids set.
      2. core_attach.cleanup_orphan_attachments(dry_run=...) 호출.
      3. 결과 print + (실 삭제 시) P4 자동 submit (db + 사라진 파일 path).
    """
    from datetime import datetime, timedelta
    from whooing_core import attachments as core_attach
    from whooing_tui import data as tui_data
    from whooing_tui import sync
    from whooing_tui.auth import load_auth_from_env
    from whooing_tui.client import WhooingClient

    # 1. ledger fetch.
    auth = load_auth_from_env()
    client = WhooingClient(auth)
    section_id = await _resolve_section_id(client, args.section_id)
    end_d = datetime.now()
    start_d = end_d - timedelta(days=max(1, args.days))
    entries = await client.list_entries(
        section_id=section_id,
        start_date=start_d.strftime("%Y%m%d"),
        end_date=end_d.strftime("%Y%m%d"),
    )
    valid = {str(e.get("entry_id")) for e in entries if e.get("entry_id")}
    print(
        f"섹션 {section_id} 의 최근 {args.days}일 거래 {len(entries)}건 fetch — "
        f"valid_entry_id {len(valid)}개"
    )

    # 2. cleanup.
    tui_data.init_shared_schema()
    root = tui_data.attachments_root()
    deleted_paths: list = []
    with tui_data.open_rw() as conn:
        result = core_attach.cleanup_orphan_attachments(
            conn, valid,
            attachments_root=root,
            delete_files=not args.keep_files,
            dry_run=args.dry_run,
        )
        # 사라진 파일 path 수집 — P4 submit 용.
        if not args.dry_run and not args.keep_files:
            for o in result["orphans"]:
                # cleanup 이 실제 unlink 했는지는 result 에 없지만 path 는 명확.
                deleted_paths.append(root / o["file_path"])

    # 3. 보고.
    print(
        f"orphan {result['orphan_count']}건 발견 — "
        f"db row 삭제 {result['rows_deleted']} / "
        f"파일 삭제 {result['files_deleted']} / "
        f"dedup 보존 {result['files_kept_dedup']}"
        + (" (DRY RUN)" if args.dry_run else "")
    )
    for o in result["orphans"][:10]:
        print(
            f"  - id={o['id']} entry={o['entry_id']} "
            f"file={o['file_path']} {o.get('original_filename') or ''}"
        )
    if len(result["orphans"]) > 10:
        print(f"  ... +{len(result['orphans']) - 10} more")

    # 4. 동기화 submit (실 삭제 시만, 백엔드 활성 시). 'none' 이면 no-op.
    if not args.dry_run and result["rows_deleted"] > 0:
        sync.submit_files(
            [tui_data.db_path(), *deleted_paths],
            f"[whooing-tui] gc-attachments: orphan {result['rows_deleted']} rows + "
            f"{result['files_deleted']} files (section={section_id})",
            blocking=True,
        )
    return 0


def _cmd_export_attachments(args: argparse.Namespace) -> int:
    """CL #51146+ (A17) — entry 또는 section 의 첨부를 zip 으로 export.

    포함:
      - 디스크 파일들 (각 첨부의 file_path).
      - `manifest.json` — entry/section/sha256/size/note 등 메타.
    """
    import json
    import zipfile
    from pathlib import Path
    from whooing_core import attachments as core_attach
    from whooing_tui import data as tui_data

    if not args.entry_id and not args.section_id:
        print("error: --entry 또는 --section 중 하나 필수", file=sys.stderr)
        return 2
    if args.entry_id and args.section_id:
        print("error: --entry / --section 동시 지정 불가", file=sys.stderr)
        return 2

    tui_data.init_shared_schema()
    root = tui_data.attachments_root()
    out_path = Path(args.out_path).expanduser().resolve()

    rows: list[dict[str, Any]] = []
    with tui_data.open_ro() as conn:
        if args.entry_id:
            m = core_attach.list_attachments_for(
                conn, [args.entry_id], section_id=args.section_id,
            )
            rows = m.get(args.entry_id, [])
        else:
            # section 전체 — 모든 entry_id 의 첨부.
            cur = conn.execute(
                "SELECT * FROM entry_attachments WHERE section_id = ? "
                "ORDER BY entry_id, attached_at",
                (args.section_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        print(
            f"export 할 첨부 0건 (entry={args.entry_id or '-'}, "
            f"section={args.section_id or '-'})"
        )
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            full = root / r["file_path"]
            if not full.exists():
                skipped += 1
                continue
            arcname = f"files/{r['file_path']}"
            zf.write(full, arcname=arcname)
            written += 1
        # manifest — caller 가 entry_id / sha256 / note 등을 zip 안에서 회수.
        manifest = {
            "schema_version": tui_data.schema_version(),
            "entry_id": args.entry_id,
            "section_id": args.section_id,
            "rows": rows,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    print(
        f"export 완료: {out_path} — {written} 파일 + manifest "
        f"(skipped: {skipped})"
    )
    return 0


async def _dispatch_async(args: argparse.Namespace) -> int:
    if args.command == "sections" and args.sections_command == "list":
        return await _cmd_sections_list(args)
    if args.command == "accounts" and args.accounts_command == "list":
        return await _cmd_accounts_list(args)
    if args.command == "entries" and args.entries_command == "list":
        return await _cmd_entries_list(args)
    if args.command == "gc-attachments":
        return await _cmd_gc_attachments(args)
    if args.command == "export-attachments":
        # CL #51146+ (A17) — sync (zip + db SELECT 만, 후잉 호출 없음).
        return _cmd_export_attachments(args)
    # parser 가 required=True 로 막기 때문에 도달 불가
    raise ToolError("INTERNAL", f"unknown command path: {args!r}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    # 동기화 백엔드 결정 (env > config > 기본 'none'). 서브커맨드의 sync
    # 호출(gc-attachments 등)이 본 설정을 따른다. TUI 경로는 app.on_mount
    # 가 다시 configure 하지만 idempotent.
    try:
        from whooing_tui import sync
        sync.configure(sync.resolve(load_config()))
    except Exception:  # pragma: no cover
        log.debug("sync backend 결정 실패 — 기본(none)", exc_info=True)

    # No subcommand → TUI launch.
    if args.command is None:
        try:
            from whooing_tui.app import run_app
        except ImportError as e:
            print(
                f"error: TUI 의존성 누락 — `pip install -e .[dev]` 또는 textual 설치 필요 ({e})",
                file=sys.stderr,
            )
            return 6
        return run_app()

    try:
        return asyncio.run(_dispatch_async(args))
    except ValueError as e:
        # parse_yyyymmdd 등의 입력 검증 실패
        print(f"error [USER_INPUT] {e}", file=sys.stderr)
        return 2
    except ToolError as e:
        print(f"error [{e.kind}] {e.message}", file=sys.stderr)
        if e.details:
            log.debug("details: %s", sanitize_for_log(e.details))
        return _EXIT_BY_KIND.get(e.kind, 5)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
