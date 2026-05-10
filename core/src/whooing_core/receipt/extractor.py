"""영수증 / 인보이스 PDF 의 (date, amount, merchant) 추출.

CL #51128+. 이미지가 아닌 *텍스트 추출 가능한* PDF 만 (pdfplumber).

설계 원칙:
- regex 만 — 외부 LLM 호출 없음.
- 우선순위: 더 견고한 형식 (lines + keyword 컨텍스트) 우선, fallback 으로
  bare 숫자 / 첫 줄.
- "확실한 결과" + "추측 결과" 를 ReceiptInfo 의 confidence 로 caller 에
  전달 — TUI 는 confidence 가 낮으면 사용자 확인 강조.
- 빈 결과 (None) 도 정상 — 사용자가 dialog 에서 직접 채울 수 있게.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class ReceiptInfo:
    """PDF 에서 뽑아낸 영수증 핵심 필드.

    date: YYYYMMDD 8 글자 (후잉 entry_date 형식). None 이면 미발견.
    amount: 정수 (KRW). None 이면 미발견.
    merchant: 가맹점/회사명 추정 — 첫 번째 비빈 줄, 키워드 ("상호:" 등)
      기반 추출. None 이면 미발견.
    confidence: 0.0 ~ 1.0. 1.0 = 키워드 컨텍스트 매칭, 0.5 = pure regex,
      0.0 = 미발견 또는 fallback (filename stem 등).
    raw_text: 추출 원문 — 디버깅 / 사용자가 dialog 에서 직접 보고 수정.
    source_file: 원본 PDF 의 절대 경로.
    """

    date: str | None
    amount: int | None
    merchant: str | None
    confidence: float
    raw_text: str
    source_file: str


# ---- 정규식 모음 -----------------------------------------------------------

# 날짜: YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YYYY년 MM월 DD일.
# 한국 영수증은 YYYY 가 4자리 + 한국 한자/한글 단위로 자주 나타남.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(20\d{2})\s*[년\-./]\s*(\d{1,2})\s*[월\-./]\s*(\d{1,2})\s*일?"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})"),  # YYYYMMDD bare — 마지막에 시도.
)

# 금액: 우선순위 (높음→낮음):
#   1. 키워드 ("합계", "총액", "결제금액", "Total", ...) 다음에 나오는 숫자.
#   2. ₩ 또는 KRW 가 붙은 숫자.
#   3. "원" 단위 명시 숫자.
#   4. 콤마 포함 큰 숫자 — fallback (가장 큰 값을 선택).
_AMOUNT_KEYWORDS: tuple[str, ...] = (
    "합계", "총액", "총합계", "결제금액", "결제 금액", "청구금액",
    "Total", "TOTAL", "GRAND TOTAL", "Amount", "Subtotal",
)

# 숫자 본체: "1,234" / "12,345,678" / "1234" — 정수만 (소수점 무시 — KRW 가정).
_NUMBER_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)")
# 통화 마크 명시.
_CURRENCY_RE = re.compile(
    r"(?:₩|￦|\\|KRW)\s*(\d{1,3}(?:,\d{3})+|\d+)|"
    r"(\d{1,3}(?:,\d{3})+|\d+)\s*원"
)

# 가맹점 후보 키워드 — ":" 또는 ":" 다음 한 줄.
_MERCHANT_KEYWORDS: tuple[str, ...] = (
    "상호", "상호명", "가맹점", "가맹점명", "Merchant", "Vendor", "Store",
    "Company", "Business",
)


def find_date_in_text(text: str) -> tuple[str | None, float]:
    """text 안에서 첫 번째 그럴듯한 날짜 → YYYYMMDD + confidence.

    `_DATE_PATTERNS` 의 첫 매칭. 두 번째 (bare YYYYMMDD) 는 confidence 0.4
    — 다른 8자리 숫자 (계좌번호 등) 와 혼동 가능.
    """
    if not text:
        return None, 0.0
    for i, pat in enumerate(_DATE_PATTERNS):
        m = pat.search(text)
        if m is None:
            continue
        try:
            year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3))
        except (ValueError, IndexError):
            continue
        if not (2000 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
            continue
        confidence = 1.0 if i == 0 else 0.4
        return f"{year:04d}{month:02d}{day:02d}", confidence
    return None, 0.0


def find_amount_in_text(text: str) -> tuple[int | None, float]:
    """text 안에서 가장 그럴듯한 금액 → int + confidence.

    우선순위:
      1.0  키워드 ("합계"/"Total" 등) 와 같은 줄 + 숫자.
      0.8  ₩ / KRW / "원" 마크.
      0.5  콤마 포함 큰 숫자 fallback (가장 큰 값).
    """
    if not text:
        return None, 0.0

    # 1. 키워드 + 같은 줄 숫자.
    for line in text.splitlines():
        if not any(kw in line for kw in _AMOUNT_KEYWORDS):
            continue
        # 키워드 뒤 최대 50자 안의 숫자 — 콤마 포함.
        for kw in _AMOUNT_KEYWORDS:
            idx = line.find(kw)
            if idx < 0:
                continue
            tail = line[idx + len(kw): idx + len(kw) + 80]
            for m in _NUMBER_RE.finditer(tail):
                try:
                    n = int(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                if n >= 100:  # 100원 미만은 거절 (포인트 잔액 등 noise 회피).
                    return n, 1.0

    # 2. 통화 마크.
    candidates: list[int] = []
    for m in _CURRENCY_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        try:
            n = int(raw.replace(",", ""))
        except (ValueError, AttributeError):
            continue
        if n >= 100:
            candidates.append(n)
    if candidates:
        return max(candidates), 0.8

    # 3. 콤마 포함 큰 숫자 fallback.
    fallback: list[int] = []
    for m in _NUMBER_RE.finditer(text):
        if "," not in m.group(1):
            continue
        try:
            n = int(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if n >= 1000:  # 콤마 있다는 건 4자리 이상.
            fallback.append(n)
    if fallback:
        return max(fallback), 0.5

    return None, 0.0


def find_merchant_in_text(text: str) -> tuple[str | None, float]:
    """가맹점/회사명 추정.

    1.0  "상호: XXX" / "Merchant: XXX" 패턴.
    0.5  첫 번째 비빈 줄 (영수증 헤더 가정).
    0.0  미발견.
    """
    if not text:
        return None, 0.0
    # 키워드는 *긴 것 먼저* 시도 — "가맹점명" 이 "가맹점" 보다 우선이라야
    # "가맹점명：스타벅스" 의 "가맹점" 매칭으로 "명：스타벅스" 가 캡쳐되는 회귀 방지.
    sorted_kw = sorted(_MERCHANT_KEYWORDS, key=len, reverse=True)
    for line in text.splitlines():
        for kw in sorted_kw:
            # ":" 또는 공백 + 값.
            m = re.search(rf"{re.escape(kw)}\s*[:：]?\s*(.+?)\s*$", line, re.IGNORECASE)
            if m and m.group(1).strip():
                value = m.group(1).strip()
                # 너무 길면 자름 (영수증 헤더 line 끝의 숫자/날짜 혼입 방지).
                if len(value) > 60:
                    value = value[:60]
                return value, 1.0
    # 2. 첫 비빈 줄 — 너무 길거나 숫자 위주면 skip.
    for line in text.splitlines():
        s = line.strip()
        if not s or len(s) > 60:
            continue
        if sum(ch.isdigit() for ch in s) > len(s) // 2:
            continue
        return s, 0.5
    return None, 0.0


def extract_receipt(pdf_path: str | Path) -> ReceiptInfo:
    """PDF → ReceiptInfo. 텍스트 추출 실패 시도 raise X — 빈 결과 반환.

    호출자 (TUI) 는 None 필드를 dialog 에서 사용자에게 직접 입력받음.
    """
    src = Path(pdf_path).expanduser().resolve()
    raw = ""
    try:
        # pdf_adapters.base 의 helper 재사용.
        from whooing_core.pdf_adapters.base import extract_first_page_text
        raw = extract_first_page_text(str(src))
    except Exception:  # pragma: no cover — 비밀번호 / 손상 / 이미지 등.
        log.exception("pdf 텍스트 추출 실패: %s", src)
        raw = ""

    date, dc = find_date_in_text(raw)
    amount, ac = find_amount_in_text(raw)
    merchant, mc = find_merchant_in_text(raw)
    if merchant is None:
        # filename stem fallback — 사용자가 파일명으로 가맹점을 짐작 가능.
        merchant = src.stem
    # 전체 confidence 는 3 필드의 평균 (없으면 0).
    n = sum(1 for c in (dc, ac, mc) if c > 0) or 1
    confidence = (dc + ac + mc) / n
    return ReceiptInfo(
        date=date, amount=amount, merchant=merchant,
        confidence=confidence, raw_text=raw, source_file=str(src),
    )
