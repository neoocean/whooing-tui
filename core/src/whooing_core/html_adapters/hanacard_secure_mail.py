"""하나카드 보안메일 (CryptoJS AES) HTML adapter.

CL #52940+ (재작성, 사용자 보고): 종전 parser 는 `splitlines()` 로 평문화한
뒤 MM/DD 패턴 오프셋으로 row 를 추출 — 외화 / 할부 상세 / 다중 카드 섹션
에서 잘못된 row 가 다수 추출 ("USA" 가 가맹점, "일시불" 이 가맹점 등).

본 모듈은 **HTML 테이블 구조** 를 직접 파싱:
  - 메인 거래표 (header: "이용가맹점(은행)" + "이번 달 결제하실 금액"):
    각 row 가 한 거래. cells[0]=date(MM/DD), cells[1]=merchant,
    cells[2]=이용금액, cells[7]=이용혜택, cells[8]=혜택금액.
  - 매출취소표 (header: "취소일자"):
    cells[0]=이용일자, cells[2]=가맹점, cells[4]=이용금액 (음수).
  - 해외이용표 (header: "환율" / "통화 구분"):
    *건너뜀* — 메인 표가 동일 거래를 KRW 환산한 값으로 이미 포함.

지원 패턴:
  - 일반 거래: 양수 금액.
  - 할인 (cells[7]=="할인"): 혜택금액 (cells[8], 음수) 사용.
  - 부분 취소 (메인 표에서 음수 금액): 그대로.
  - 매출취소 (별도 표): 그대로.
  - 외화 거래 (메인 표 안의 30,083 / 21,865 같은 KRW 환산값).

이용대금명세서 작성일의 연도를 사용 (e.g., "2026년 5월 27일" → 2026).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from whooing_core.csv_adapters.base import CSVRow
from whooing_core.html_adapters.base import (
    HtmlDecryptError,
    decrypt_html_with_playwright,
)

log = logging.getLogger(__name__)

ISSUER = "hanacard_secure_mail"


def is_match(head_text: str) -> bool:
    """첫 8KB 텍스트로 issuer 식별. encrypted HTML 라도 title/script 키워드는 노출됨."""
    return all(kw in head_text for kw in ["하나", "보안", "uni_func", "CryptoJS"]) \
        or ("하나카드" in head_text and "uni_func" in head_text)


def parse_html(html_path: str, password: str | None) -> list[CSVRow]:
    """복호화 → 거래 추출."""
    if not password:
        raise HtmlDecryptError("hanacard_secure_mail: password 필수")

    loop = asyncio.get_event_loop()
    if loop.is_running():
        raise HtmlDecryptError(
            "parse_html 은 sync 호출 전용. 도구에서는 parse_html_async 사용."
        )
    decrypted = loop.run_until_complete(
        decrypt_html_with_playwright(html_path, password)
    )
    return extract_rows_from_decrypted(decrypted)


async def parse_html_async(html_path: str, password: str) -> list[CSVRow]:
    """tool 에서 호출 (비동기). decrypt + parse 한 번에."""
    if not password:
        raise HtmlDecryptError("hanacard_secure_mail: password 필수")
    decrypted = await decrypt_html_with_playwright(html_path, password)
    return extract_rows_from_decrypted(decrypted)


# ---- parsing -----------------------------------------------------------


_DATE_PAT = re.compile(r"^\d{2}/\d{2}$")
_AMOUNT_PAT = re.compile(r"^-?\d{1,3}(?:,\d{3})+$")
_NUM_PAT = re.compile(r"^-?\d+$")
_STATEMENT_DATE_PAT = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_USAGE_PERIOD_PAT = re.compile(
    r"일시불\s*및\s*할부\s*[:：]\s*(\d{4})\.\s*(\d{2})\.\s*\d{2}"
)


def extract_rows_from_decrypted(html: str) -> list[CSVRow]:
    """복호화된 HTML 의 transaction 표들에서 거래 추출."""
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script"):
        s.decompose()

    statement_year = _detect_year(soup)
    statement_month = _detect_month(soup)

    rows: list[CSVRow] = []
    seen_tables: set[int] = set()

    # leaf 테이블만 — 중첩 테이블의 outer 가 모든 텍스트를 평탄화해 가져가는 것
    # 방지. leaf table 은 그 안에 추가 table 이 없는 가장 안쪽 단위.
    for table in soup.find_all("table"):
        if table.find("table"):  # 중첩 outer — skip.
            continue
        # 같은 obj 두 번 처리 방지.
        if id(table) in seen_tables:
            continue
        seen_tables.add(id(table))

        kind = _classify_table(table)
        if kind == "main":
            rows.extend(_parse_main_table(
                table, statement_year, statement_month,
            ))
        elif kind == "cancellation":
            rows.extend(_parse_cancellation_table(
                table, statement_year, statement_month,
            ))
        # "foreign" 표는 메인 표에 KRW 환산값으로 이미 포함 — skip.
        # "other" / 헤더 / 요약 표는 모두 skip.

    # dedup: 같은 (date, amount, merchant prefix) 중복 row 제거.
    seen: set[tuple[str, int, str]] = set()
    unique: list[CSVRow] = []
    for r in rows:
        key = (r.date, r.amount, r.merchant[:30])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    log.info(
        "hanacard_secure_mail: %d raw → %d unique (year=%d, statement_month=%d)",
        len(rows), len(unique), statement_year, statement_month,
    )
    return unique


# 후방 호환 — 종전 명. 일부 caller 가 본 이름으로 import 가능.
_extract_rows_from_decrypted = extract_rows_from_decrypted


# ---- helpers ----------------------------------------------------------


def _detect_year(soup: BeautifulSoup) -> int:
    """이용대금명세서 작성일에서 연도. 미발견 시 현재 연도."""
    text = soup.get_text(" ", strip=True)
    m = _STATEMENT_DATE_PAT.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return datetime.now().year


def _detect_month(soup: BeautifulSoup) -> int:
    """결제월. 일시불 이용기간의 시작 월 (이용기간 종료는 결제월).

    예: "2026. 04. 15 ~ 2026. 05. 14" → 결제월 5 (5월 결제).
    """
    text = soup.get_text(" ", strip=True)
    m = _USAGE_PERIOD_PAT.search(text)
    if m:
        try:
            start_month = int(m.group(2))
            # 이용기간 종료가 결제월. 일반 카드는 시작 + 1, 마지막날 ~ 다음달.
            # 단순화: 시작 월 그대로 사용 + 1 — 결제는 보통 한 달 후.
            return (start_month % 12) + 1
        except ValueError:
            pass
    # fallback — 명세서 작성일의 월.
    m2 = _STATEMENT_DATE_PAT.search(text)
    if m2:
        try:
            return int(m2.group(2))
        except ValueError:
            pass
    return datetime.now().month


def _row_cells(row) -> list[str]:
    return [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]


def _classify_table(table) -> str:
    """테이블이 어떤 종류인지 판정.

    return:
      "main"         — 도메스틱 거래 표.
      "cancellation" — 매출취소 표.
      "foreign"      — 해외이용 표.
      "other"        — 기타 (header / 요약 등) — skip.
    """
    # 헤더는 보통 첫 1-2 row 에 column 명.
    rows = table.find_all("tr")
    if not rows:
        return "other"
    head_text = " ".join(
        _row_cells(r) for r in rows[:3]  # 처음 3행을 헤더 후보로.
    ) if False else " ".join(
        " ".join(_row_cells(r)) for r in rows[:3]
    )
    # 매출취소 — 가장 구별되는 키워드.
    if "취소일자" in head_text:
        return "cancellation"
    # 해외이용 — "환율" + "이용원금" 의 조합.
    if "환율" in head_text and "이용원금" in head_text:
        return "foreign"
    # 메인 — "이용가맹점" + "이번 달 결제" / "이용금액".
    if (
        "이용가맹점" in head_text
        and ("이번 달 결제" in head_text or "이용금액" in head_text)
    ):
        return "main"
    return "other"


def _parse_money(s: str) -> int | None:
    """'15,000' / '-15,000' / '60' → int. 매칭 안 되면 None."""
    s = (s or "").strip()
    if not s:
        return None
    if not (_AMOUNT_PAT.match(s) or _NUM_PAT.match(s)):
        return None
    sign = -1 if s.startswith("-") else 1
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    return sign * int(digits)


def _md_to_yyyymmdd(
    md: str, statement_year: int, statement_month: int,
) -> str | None:
    """MM/DD + 명세서 연도/월 → YYYYMMDD.

    명세서가 5월 작성이면 거래 MM 은 *대부분* statement_month 또는 그 이전.
    MM > statement_month + 1 이면 작년 거래로 추정 (예: 명세서 2026-05 인데
    행 MM=11 → 2025-11).
    """
    if not _DATE_PAT.match(md):
        return None
    try:
        mm, dd = md.split("/")
        m, d = int(mm), int(dd)
        year = statement_year
        # 명세서 월보다 *훨씬 뒤* 의 월이면 작년.
        if m > statement_month + 1:
            year = statement_year - 1
        candidate = f"{year:04d}{m:02d}{d:02d}"
        datetime.strptime(candidate, "%Y%m%d")  # validate
        return candidate
    except (ValueError, IndexError):
        return None


def _parse_main_table(
    table, year: int, month: int,
) -> list[CSVRow]:
    """메인 거래 표 파싱.

    cells layout (열 개수는 row 마다 다를 수 있음):
      [0] 이용일자 (MM/DD)
      [1] 가맹점
      [2] 이용금액
      [3] 할부 회차 / [4] 할부 기간
      [5] 이번 달 결제하실 금액 (원금)
      [6] 수수료
      [7] 이용 혜택 (예: "할인")
      [8] 혜택 금액 (음수)
      [9-] 결제후잔액 / 포인트
    """
    out: list[CSVRow] = []
    for row in table.find_all("tr"):
        cells = _row_cells(row)
        # 섹션 헤더 / 소계 / 합계 / 가맹점 상세 등 — 첫 cell 이 날짜가 아니면 skip.
        if not cells or not _DATE_PAT.match(cells[0]):
            continue

        date = _md_to_yyyymmdd(cells[0], year, month)
        if not date:
            continue
        merchant = cells[1] if len(cells) > 1 else ""
        if not merchant:
            continue

        amount_use = _parse_money(cells[2]) if len(cells) > 2 else None
        benefit_kind = cells[7] if len(cells) > 7 else ""
        benefit_amt = _parse_money(cells[8]) if len(cells) > 8 else None
        # 할인 row — 혜택금액 (음수) 가 effective amount.
        if benefit_kind == "할인" and benefit_amt is not None:
            amount = benefit_amt
        elif amount_use is not None:
            amount = amount_use
        else:
            continue

        # 0원 row 는 의미 없음 (헤더 잔재 등) — skip.
        if amount == 0:
            continue

        out.append(CSVRow(
            date=date, amount=amount, merchant=merchant,
            raw={
                "table": "main",
                "amount_use": str(amount_use or ""),
                "benefit_kind": benefit_kind,
                "benefit_amt": str(benefit_amt or ""),
            },
        ))
    return out


def _parse_cancellation_table(
    table, year: int, month: int,
) -> list[CSVRow]:
    """매출취소 표 파싱.

    cells layout:
      [0] 이용일자 (MM/DD)
      [1] 취소일자 (MM/DD)
      [2] 가맹점
      [3] 사용카드
      [4] 이용금액 (음수)
      [5] 미화금액
    """
    out: list[CSVRow] = []
    for row in table.find_all("tr"):
        cells = _row_cells(row)
        if len(cells) < 5:
            continue
        # cells[0] 이 날짜인 row 만.
        if not _DATE_PAT.match(cells[0]):
            continue
        date = _md_to_yyyymmdd(cells[0], year, month)
        if not date:
            continue
        merchant = cells[2]
        if not merchant:
            continue
        amount = _parse_money(cells[4])
        if amount is None or amount == 0:
            continue
        out.append(CSVRow(
            date=date, amount=amount, merchant=merchant,
            raw={"table": "cancellation"},
        ))
    return out
