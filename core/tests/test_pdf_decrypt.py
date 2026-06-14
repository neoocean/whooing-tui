"""PDF 복호화 (pdf_adapters.decrypt) 단위 테스트.

통신사 이메일 명세서처럼 user password 로 보호된 PDF 를 복호화하는 기능.
fixture 없이 pikepdf 로 암호화 PDF 를 즉석 생성해 검증한다.
"""

from __future__ import annotations

import pikepdf
import pytest

from whooing_core.pdf_adapters import (
    PdfDecryptError,
    decrypt_pdf,
    is_encrypted,
)
from whooing_core.pdf_adapters.base import extract_first_page_text

PASSWORD = "820115"  # 주민번호 앞 6자리(생년월일) 관례


def _make_pdf(path: str, *, password: str | None) -> None:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    if password is None:
        pdf.save(path)
    else:
        pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=password))


@pytest.fixture
def encrypted_pdf(tmp_path):
    p = tmp_path / "enc.pdf"
    _make_pdf(str(p), password=PASSWORD)
    return str(p)


@pytest.fixture
def plain_pdf(tmp_path):
    p = tmp_path / "plain.pdf"
    _make_pdf(str(p), password=None)
    return str(p)


def test_is_encrypted_true(encrypted_pdf):
    assert is_encrypted(encrypted_pdf) is True


def test_is_encrypted_false(plain_pdf):
    assert is_encrypted(plain_pdf) is False


def test_decrypt_roundtrip(encrypted_pdf, tmp_path):
    out = tmp_path / "out.pdf"
    result = decrypt_pdf(encrypted_pdf, PASSWORD, str(out))
    assert result == str(out)
    assert out.is_file()
    # 복호화본은 비밀번호 없이 열린다.
    assert is_encrypted(str(out)) is False
    with pikepdf.open(str(out)) as pdf:  # raises if still encrypted
        assert len(pdf.pages) == 1


def test_decrypt_default_out_path(encrypted_pdf):
    out = decrypt_pdf(encrypted_pdf, PASSWORD)
    assert out.endswith(".pdf")
    assert is_encrypted(out) is False


def test_decrypt_wrong_password_raises(encrypted_pdf):
    with pytest.raises(PdfDecryptError):
        decrypt_pdf(encrypted_pdf, "000000")


def test_decrypt_missing_file_raises(tmp_path):
    with pytest.raises(PdfDecryptError):
        decrypt_pdf(str(tmp_path / "nope.pdf"), PASSWORD)


def test_decrypt_plain_pdf_passthrough(plain_pdf, tmp_path):
    # 이미 평문이어도 사본을 만들어 반환 (호출부 분기 제거).
    out = tmp_path / "copy.pdf"
    result = decrypt_pdf(plain_pdf, "ignored", str(out))
    assert result == str(out)
    assert out.is_file()


def test_extract_first_page_with_password(encrypted_pdf):
    # base 추출기도 password 를 받아 보호 PDF 를 연다 (빈 페이지라 텍스트 없음).
    text = extract_first_page_text(encrypted_pdf, password=PASSWORD)
    assert text == ""
