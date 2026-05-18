"""core/preview.py — 파일 미리보기 추출 단위 테스트."""

from __future__ import annotations

import pytest

from whooing_core.preview import (
    extract_preview_text,
    is_previewable,
)


# ---- is_previewable -------------------------------------------------


@pytest.mark.parametrize("mime", [
    "text/plain", "text/markdown", "text/csv", "text/html",
    "text/xml", "application/json", "application/xml",
    "application/yaml",
])
def test_is_previewable_text_mimes(mime):
    assert is_previewable(mime) is True


def test_is_previewable_pdf_mime():
    assert is_previewable("application/pdf") is True


@pytest.mark.parametrize("mime", [
    "image/jpeg", "image/png", "video/mp4", "application/zip",
    "application/octet-stream",
])
def test_is_previewable_binary_mimes_false(mime):
    assert is_previewable(mime) is False


def test_is_previewable_by_extension_when_mime_absent():
    assert is_previewable(None, "notes.md") is True
    assert is_previewable(None, "data.csv") is True
    assert is_previewable(None, "invoice.pdf") is True
    assert is_previewable(None, "config.json") is True
    assert is_previewable(None, "photo.jpg") is False
    assert is_previewable(None, "movie.mp4") is False


def test_is_previewable_unknown_text_mime_prefix():
    """text/* 어떤 sub-type 이든 OK — generic fallback."""
    assert is_previewable("text/x-custom") is True


def test_is_previewable_both_none_returns_false():
    assert is_previewable(None, None) is False


# ---- extract_preview_text — text 파일 ----------------------------


def test_extract_text_utf8(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("안녕하세요\n2번째 줄", encoding="utf-8")
    out = extract_preview_text(p)
    assert out is not None
    assert "안녕하세요" in out
    assert "2번째 줄" in out


def test_extract_text_cp949_fallback(tmp_path):
    """utf-8 decode 실패 → cp949 fallback."""
    p = tmp_path / "win.txt"
    p.write_bytes("안녕".encode("cp949"))
    out = extract_preview_text(p, mime="text/plain")
    assert out is not None
    assert "안녕" in out


def test_extract_text_caps_long_content(tmp_path):
    p = tmp_path / "big.txt"
    long = "x" * 1_000_000
    p.write_text(long, encoding="utf-8")
    out = extract_preview_text(p, mime="text/plain", cap_chars=1000)
    assert out is not None
    assert len(out) <= 1000 + 200  # cap + suffix message
    assert "잘림" in out


def test_extract_text_uncapped(tmp_path):
    p = tmp_path / "big.txt"
    long = "y" * 5000
    p.write_text(long, encoding="utf-8")
    out = extract_preview_text(p, mime="text/plain", cap_chars=0)
    assert out is not None
    assert len(out) == 5000
    assert "잘림" not in out


def test_extract_text_missing_file_returns_none(tmp_path):
    assert extract_preview_text(tmp_path / "nope.txt") is None


def test_extract_text_unsupported_binary_returns_none(tmp_path):
    p = tmp_path / "img.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # JPEG header
    assert extract_preview_text(p, mime="image/jpeg") is None


# ---- extract_preview_text — PDF -----------------------------------


def _make_minimal_pdf(path):
    """reportlab 으로 단일 페이지 PDF 생성 (text='hello pdf')."""
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, "hello pdf preview test")
    c.showPage()
    c.save()


def test_extract_pdf_text(tmp_path):
    pdf = tmp_path / "x.pdf"
    _make_minimal_pdf(pdf)
    out = extract_preview_text(pdf, mime="application/pdf")
    assert out is not None
    assert "hello pdf preview test" in out
    assert "Page 1/1" in out


def test_extract_pdf_by_extension_when_mime_missing(tmp_path):
    pdf = tmp_path / "no-mime.pdf"
    _make_minimal_pdf(pdf)
    out = extract_preview_text(pdf)  # mime 미지정 — .pdf 확장자로 인식
    assert out is not None
    assert "hello pdf preview test" in out
