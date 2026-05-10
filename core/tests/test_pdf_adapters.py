"""PDF adapter detect + parse — pure parser unit tests.

reconcile / pdf_format_detect (tool layer) 는 wrapper repo 잔류.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_core.pdf_adapters import detect, known_issuers, parse

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"


def test_known_issuers():
    assert "shinhan_card" in known_issuers()
    assert "hyundai_card" in known_issuers()


def test_detect_shinhan():
    d = detect(str(FIXTURES / "shinhan_sample.pdf"))
    assert d.detected_issuer == "shinhan_card"
    assert d.confidence > 0.5
    assert "신한카드" in d.first_page_excerpt


def test_detect_hyundai():
    d = detect(str(FIXTURES / "hyundai_sample.pdf"))
    assert d.detected_issuer == "hyundai_card"
    assert d.confidence > 0.5


def test_parse_shinhan_rows():
    issuer, rows = parse(str(FIXTURES / "shinhan_sample.pdf"))
    assert issuer == "shinhan_card"
    assert len(rows) == 3
    r = rows[0]
    assert r.date == "20260509"
    assert r.amount == 6200
    assert "스타벅스" in r.merchant


def test_parse_hyundai_rows():
    issuer, rows = parse(str(FIXTURES / "hyundai_sample.pdf"))
    assert issuer == "hyundai_card"
    assert len(rows) == 2


def test_parse_explicit_issuer():
    issuer, _ = parse(str(FIXTURES / "shinhan_sample.pdf"), issuer="shinhan_card")
    assert issuer == "shinhan_card"


def test_parse_unknown_issuer_raises():
    with pytest.raises(ValueError):
        parse(str(FIXTURES / "shinhan_sample.pdf"), issuer="lotte_card")
