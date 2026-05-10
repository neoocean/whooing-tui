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

async def _dispatch_async(args: argparse.Namespace) -> int:
    if args.command == "sections" and args.sections_command == "list":
        return await _cmd_sections_list(args)
    if args.command == "accounts" and args.accounts_command == "list":
        return await _cmd_accounts_list(args)
    if args.command == "entries" and args.entries_command == "list":
        return await _cmd_entries_list(args)
    # parser 가 required=True 로 막기 때문에 도달 불가
    raise ToolError("INTERNAL", f"unknown command path: {args!r}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

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
