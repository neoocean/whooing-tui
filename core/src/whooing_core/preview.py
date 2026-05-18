"""파일 미리보기 텍스트 추출 — TUI 의 첨부 화면이 사용.

사용자가 첨부 row 위에서 Enter 누르면 그 파일의 내용을 한 modal 로 보여
준다. 지원 type:
  - text/* (plain, markdown, csv, html, xml, json, yaml ...)
  - application/pdf — pdfplumber 로 페이지별 추출.

binary (image / video / archive 등) 은 None — caller 가 "미리보기 불가"
안내. 외부 viewer (`o` 키 = `open` / `xdg-open`) 으로 우회.

CL #52750+.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

log = logging.getLogger(__name__)

_TEXT_MIMES: Final = frozenset({
    "text/plain", "text/markdown", "text/csv", "text/html",
    "text/xml", "text/x-python", "text/x-yaml",
    "application/json", "application/xml", "application/yaml",
    "application/x-yaml",
})

_TEXT_SUFFIXES: Final = frozenset({
    ".txt", ".md", ".rst", ".csv", ".tsv", ".log",
    ".json", ".xml", ".yml", ".yaml",
    ".html", ".htm",
    ".py", ".js", ".ts", ".sh", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".rb", ".sql", ".toml", ".ini", ".cfg",
})

_DEFAULT_CAP_CHARS: Final = 200_000


def is_previewable(mime: str | None, filename: str | None = None) -> bool:
    """미리보기 지원 여부 — mime 또는 확장자 기준.

    Args:
      mime: 'text/plain' / 'application/pdf' 같은 MIME 문자열 (선택).
      filename: 'invoice.pdf' 같은 파일명 (선택, mime 미상 시 확장자 사용).
    """
    if mime:
        if mime in _TEXT_MIMES or mime.startswith("text/"):
            return True
        if mime == "application/pdf":
            return True
    if filename:
        suf = Path(filename).suffix.lower()
        if suf == ".pdf":
            return True
        if suf in _TEXT_SUFFIXES:
            return True
    return False


def extract_preview_text(
    path: str | Path,
    *,
    mime: str | None = None,
    cap_chars: int = _DEFAULT_CAP_CHARS,
) -> str | None:
    """파일 → 미리보기 텍스트. 지원 안 하는 type / 부재 / 오류면 None.

    Args:
      path: 파일 절대 경로.
      mime: MIME 추정 (선택). 없으면 확장자 fallback.
      cap_chars: 최대 글자 수 (메모리 보호). <=0 면 무제한.

    Returns:
      텍스트 (text 파일) 또는 페이지별 추출 (PDF). None 이면 caller 가
      "미리보기 불가" 안내.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        log.debug("preview: 파일 부재 — %s", p)
        return None
    suf = p.suffix.lower()
    is_pdf = mime == "application/pdf" or suf == ".pdf"
    if is_pdf:
        return _extract_pdf_text(p, cap_chars)
    if not is_previewable(mime, p.name):
        return None
    return _read_text(p, cap_chars)


def _read_text(p: Path, cap: int) -> str | None:
    """텍스트 파일 read — UTF-8 우선, cp949 / latin-1 fallback."""
    try:
        data = p.read_bytes()
    except OSError as ex:
        log.debug("preview: read 실패 — %s: %s", p, ex)
        return None
    text: str | None = None
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    if cap > 0 and len(text) > cap:
        text = (
            text[:cap]
            + f"\n\n[잘림 — 파일 {len(data):,} bytes 중 첫 {cap:,} 글자]"
        )
    return text


def _extract_pdf_text(p: Path, cap: int) -> str | None:
    """PDF → 페이지별 텍스트. pdfplumber 미설치 / 추출 실패 면 None."""
    try:
        import pdfplumber  # type: ignore[import-untyped]
    except ImportError:
        log.debug("preview: pdfplumber 미설치 — PDF skip")
        return None
    parts: list[str] = []
    try:
        with pdfplumber.open(str(p)) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                try:
                    txt = page.extract_text() or ""
                except Exception:  # pragma: no cover
                    txt = "[이 페이지 추출 실패]"
                parts.append(f"━━━ Page {i}/{total} ━━━\n{txt}")
                if cap > 0 and sum(len(s) for s in parts) > cap:
                    parts.append(f"\n[잘림 — {total} pages 중 {i} 까지만]")
                    break
    except Exception as ex:
        log.debug("preview: PDF 추출 실패 — %s: %s", p, ex)
        return None
    if not parts:
        return None
    text = "\n\n".join(parts)
    if cap > 0 and len(text) > cap:
        text = text[:cap] + "\n\n[잘림]"
    return text
