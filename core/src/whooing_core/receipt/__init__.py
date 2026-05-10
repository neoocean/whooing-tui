"""영수증 / 인보이스 PDF 의 핵심 정보 추출.

CL #51128+ 사용자 요청:
> 여러 서비스로부터 오는 영수증, 인보이스 pdf를 읽어 이 파일에 해당하는 거래를
> 자동으로 찾아 첨부하는 기능을 만들어주세요. 만약 해당하는 거래가 아직
> 입력되지 않았다면 거래내역을 직접 제안하고 사용자의 조작에 의해 입력되게
> 해주세요.

본 모듈은 *추출* 만 책임진다 — 후잉 거래 매칭 / 첨부 / dialog 는 TUI 가.

지원 포맷:
- 텍스트 추출 가능한 PDF (pdfplumber). 이미지/스캔 PDF 는 OCR 필요 — deferred.
"""

from whooing_core.receipt.extractor import (
    ReceiptInfo,
    extract_receipt,
    find_amount_in_text,
    find_date_in_text,
    find_merchant_in_text,
)

__all__ = [
    "ReceiptInfo",
    "extract_receipt",
    "find_amount_in_text",
    "find_date_in_text",
    "find_merchant_in_text",
]
