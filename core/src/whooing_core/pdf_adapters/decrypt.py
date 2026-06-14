"""PDF 복호화 — 비밀번호 보호 PDF(통신사 이메일 명세서 등)를 평문 PDF 로.

HTML 명세서(`html_adapters.decrypt_html_with_playwright`)와 짝이 되는 PDF
버전. 차이는 매체뿐 — HTML 은 JS client-side 복호화라 Playwright 가 필요하지만,
PDF 는 표준 암호화(RC4/AES)라 pikepdf(libqpdf)로 user password 를 검증하고
암호를 제거한 사본을 만든다.

통신사(KT 등) 이메일 명세서의 문서열기 비밀번호는 보통 **주민번호 앞 6자리
(생년월일 YYMMDD)**. HTML 카드 명세서 비밀번호 관례(YYMMDD)와 동일하다.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class PdfDecryptError(Exception):
    """복호화 실패 — 잘못된 password / 손상 PDF / pikepdf 미설치 등."""


def _require_pikepdf():
    try:
        import pikepdf  # lazy import — pikepdf(libqpdf) 는 무거운 C 확장.
    except ImportError as ex:  # pragma: no cover - 환경 의존
        raise PdfDecryptError(
            "pikepdf 미설치. `pip install pikepdf` (또는 `make install`)."
        ) from ex
    return pikepdf


def is_encrypted(pdf_path: str) -> bool:
    """PDF 가 비밀번호/암호화로 보호돼 있는지.

    user password 가 걸려 무 password 로 못 여는 PDF 도 True 로 본다.
    """
    pikepdf = _require_pikepdf()
    try:
        with pikepdf.open(pdf_path) as pdf:
            return bool(pdf.is_encrypted)
    except pikepdf.PasswordError:
        return True
    except Exception as ex:
        raise PdfDecryptError(f"PDF 열기 실패: {ex}") from ex


def decrypt_pdf(
    src_path: str,
    password: str,
    out_path: str | None = None,
) -> str:
    """비밀번호 보호 PDF 를 복호화한 평문 PDF 사본으로 저장하고 그 경로 반환.

    이미 평문(암호화 아님)이어도 사본을 만들어 반환 — 호출부가 분기 없이
    "복호화본 경로"를 첨부 등에 바로 쓸 수 있게 한다.

    Args:
      src_path: 원본 PDF.
      password: 문서열기 비밀번호 (통신사 명세서는 보통 주민번호 앞 6자리).
      out_path: 출력 경로. None 이면 임시파일(`<stem>-decrypted-*.pdf`).

    Returns:
      복호화된 평문 PDF 경로.

    Raises:
      PdfDecryptError: 비밀번호 틀림 / 손상 / pikepdf 미설치 / 원본 없음.
    """
    pikepdf = _require_pikepdf()

    src = Path(src_path)
    if not src.is_file():
        raise PdfDecryptError(f"원본 PDF 없음: {src_path}")

    if out_path is None:
        fd, out_path = tempfile.mkstemp(
            prefix=f"{src.stem}-decrypted-", suffix=".pdf"
        )
        os.close(fd)

    try:
        # password 는 평문이어도 무시되므로(open 이 password 불필요 시 무시)
        # 암호화/비암호화 양쪽에 안전하다.
        with pikepdf.open(src_path, password=password) as pdf:
            pdf.save(out_path)  # encryption 미지정 → 평문 저장.
    except pikepdf.PasswordError as ex:
        raise PdfDecryptError(
            "PDF 비밀번호가 틀렸습니다. "
            "통신사 명세서는 보통 '주민번호 앞 6자리(생년월일 YYMMDD)' 입니다."
        ) from ex
    except Exception as ex:
        raise PdfDecryptError(f"PDF 복호화 실패: {ex}") from ex

    log.info("decrypted %s -> %s", src_path, out_path)
    return out_path
