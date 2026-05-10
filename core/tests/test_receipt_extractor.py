"""whooing_core.receipt.extractor — regex 기반 영수증 핵심 필드 추출.

CL #51128+. 이미지 PDF 가 아닌 텍스트 추출 결과 (str) 만으로 단위 테스트.
실 PDF 통합은 별도 (test_pdf_adapters.py) — 본 모듈은 정규식만.
"""

from __future__ import annotations

import pytest

from whooing_core.receipt.extractor import (
    find_amount_in_text,
    find_date_in_text,
    find_merchant_in_text,
)


# ---- 날짜 ---------------------------------------------------------------


def test_date_dash_format():
    d, c = find_date_in_text("결제일: 2026-05-10 12:34")
    assert d == "20260510"
    assert c == 1.0


def test_date_korean_units():
    d, c = find_date_in_text("거래일자: 2026년 5월 10일")
    assert d == "20260510"
    assert c == 1.0


def test_date_dotted_format():
    d, _ = find_date_in_text("Date 2026.05.10")
    assert d == "20260510"


def test_date_slashed_format():
    d, _ = find_date_in_text("date: 2026/05/10")
    assert d == "20260510"


def test_date_yyyymmdd_bare_low_confidence():
    """8자리 bare 숫자 — 계좌번호 등 noise 가능 → confidence 낮음."""
    d, c = find_date_in_text("주문번호 20260510")
    assert d == "20260510"
    assert c < 1.0


def test_date_takes_first_match():
    d, _ = find_date_in_text("발급 2026-05-09\n결제 2026-05-10")
    assert d == "20260509"


def test_date_rejects_invalid_month():
    """13월 등 invalid → 무시 → 다른 후보 또는 None."""
    d, _ = find_date_in_text("올해의 거래는 2026-13-01 부터")
    assert d is None


def test_date_empty_text():
    assert find_date_in_text("") == (None, 0.0)


# ---- 금액 ---------------------------------------------------------------


def test_amount_with_keyword_total():
    a, c = find_amount_in_text("합계: 12,345원")
    assert a == 12345
    assert c == 1.0


def test_amount_with_english_keyword():
    a, c = find_amount_in_text("Total 5,400")
    assert a == 5400
    assert c == 1.0


def test_amount_won_marker():
    a, c = find_amount_in_text("청구 ₩4,800")
    assert a == 4800
    assert c == 0.8


def test_amount_krw_marker():
    a, c = find_amount_in_text("KRW 12,000")
    assert a == 12000
    assert c == 0.8


def test_amount_won_suffix():
    a, c = find_amount_in_text("합계 ;;;\n4,200원")
    # 합계 키워드 같은 줄엔 숫자 없음 → 우선 통화 마크 (4,200원) → confidence 0.8.
    assert a == 4200
    assert c == 0.8


def test_amount_keyword_priority_over_other_numbers():
    """합계 다음 숫자 가 다른 큰 숫자보다 우선."""
    a, _ = find_amount_in_text(
        "주문번호 99,999,999\n합계: 1,500원\n적립금 200점"
    )
    assert a == 1500


def test_amount_fallback_largest_with_comma():
    """키워드/마크 모두 없으면 콤마 포함 가장 큰 숫자."""
    a, c = find_amount_in_text("12,000\n3,400\n500")
    assert a == 12000
    assert c == 0.5


def test_amount_rejects_small_values_under_100():
    """포인트/잔여 같은 noise 회피 — 100 미만은 거절."""
    a, _ = find_amount_in_text("Total: 50")
    assert a is None


def test_amount_empty_text():
    assert find_amount_in_text("") == (None, 0.0)


# ---- 가맹점 -------------------------------------------------------------


def test_merchant_label_korean():
    m, c = find_merchant_in_text("상호: 스타벅스 강남점\n결제: ...")
    assert m == "스타벅스 강남점"
    assert c == 1.0


def test_merchant_label_english():
    m, c = find_merchant_in_text("Merchant: McDonalds Gangnam")
    assert m == "McDonalds Gangnam"
    assert c == 1.0


def test_merchant_label_with_colon_korean():
    m, _ = find_merchant_in_text("가맹점명：스타벅스 강남점")
    assert m == "스타벅스 강남점"


def test_merchant_first_line_fallback():
    m, c = find_merchant_in_text("스타벅스 강남점\n결제일 2026-05-10")
    assert m == "스타벅스 강남점"
    assert c == 0.5


def test_merchant_skips_numeric_first_line():
    """첫 줄이 숫자 위주면 다음 줄로."""
    m, _ = find_merchant_in_text("12345-67890\n스타벅스 강남점")
    assert m == "스타벅스 강남점"


def test_merchant_truncates_long_value():
    long = "ABC" * 30  # 90 chars
    m, _ = find_merchant_in_text(f"Merchant: {long}")
    assert m is not None
    assert len(m) == 60


def test_merchant_empty_text():
    assert find_merchant_in_text("") == (None, 0.0)


# ---- 통합 — extract_receipt + filename fallback -------------------------


def test_extract_receipt_falls_back_to_filename_for_merchant(tmp_path):
    """이미지 PDF 또는 텍스트 추출 실패 → merchant 가 파일 stem 으로."""
    from whooing_core.receipt.extractor import extract_receipt
    p = tmp_path / "스타벅스영수증.pdf"
    p.write_bytes(b"")  # 텍스트 0 — pdfplumber 가 빈 결과 반환.
    info = extract_receipt(str(p))
    # 텍스트 추출 실패 (또는 빈 PDF) → merchant fallback.
    assert info.merchant == "스타벅스영수증"
    assert info.date is None
    assert info.amount is None


def test_extract_receipt_records_source_file_absolute(tmp_path):
    from whooing_core.receipt.extractor import extract_receipt
    p = tmp_path / "x.pdf"
    p.write_bytes(b"")
    info = extract_receipt(str(p))
    # 절대 경로로 기록 (TUI 의 첨부 wiring 이 그대로 사용).
    assert info.source_file == str(p.resolve())
