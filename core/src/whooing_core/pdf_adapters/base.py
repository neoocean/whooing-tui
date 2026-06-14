"""PDF 추출 공통 헬퍼.

pdfplumber 기반. 텍스트 추출 가능한 PDF 만 지원 (이미지/스캔 PDF 는 OCR 필요
— deferred). 비밀번호 PDF 는 `password` 인자로 열 수 있고(통신사 명세서 =
주민번호 앞 6자리), 생략 시 암호화 PDF 는 raise → tools 가 ToolError 로 변환.
평문 사본을 만들고 싶으면 `decrypt.decrypt_pdf` 참고.
"""

from __future__ import annotations

from dataclasses import dataclass

import pdfplumber

# 감사 2026-06 §2-D: 신뢰불가 PDF(이메일 수신 명세서)의 자원 고갈 방어.
# 카드 명세서는 수~수십 페이지. 조작된 거대 페이지 수 PDF 로 hang/OOM 하지
# 않도록 추출 페이지 수를 cap. 초과분은 무시(명세서 dedup 에는 충분).
MAX_PDF_PAGES = 200


@dataclass
class PDFDetectResult:
    detected_issuer: str | None
    confidence: float
    first_page_excerpt: str


def extract_first_page_text(pdf_path: str, password: str | None = None) -> str:
    """첫 페이지의 추출 가능한 텍스트 전체. 빈 문자열일 수 있음 (이미지 PDF).

    `password` 지정 시 비밀번호 보호 PDF 도 연다(통신사 명세서 = 주민번호 앞 6자리).
    """
    with pdfplumber.open(pdf_path, password=password or "") as pdf:
        if not pdf.pages:
            return ""
        text = pdf.pages[0].extract_text() or ""
        return text


def extract_all_tables(
    pdf_path: str, password: str | None = None
) -> list[list[list[str]]]:
    """모든 페이지의 테이블을 [page][row][col] 형태로.

    카드사 명세서는 보통 테이블이 페이지마다 이어진다. 호출자가 row level 에서
    헤더/footer/empty row 정리. `password` 로 보호 PDF 도 처리.
    """
    out: list[list[list[str]]] = []
    with pdfplumber.open(pdf_path, password=password or "") as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            for table in page.extract_tables():
                if table:
                    out.append(table)
    return out


def extract_all_text_lines(
    pdf_path: str, password: str | None = None
) -> list[str]:
    """모든 페이지의 텍스트를 줄 단위로 합친 리스트. 테이블 추출 실패 시 fallback.

    `password` 로 보호 PDF 도 처리.
    """
    out: list[str] = []
    with pdfplumber.open(pdf_path, password=password or "") as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            text = page.extract_text() or ""
            out.extend(line for line in text.splitlines() if line.strip())
    return out
