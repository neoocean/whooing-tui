"""FilePickerScreen — 디렉터리 navigation + 파일 선택 modal.

CL #51139+ (A7).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_tui.screens.file_picker import FilePickerScreen, filter_paths


# ---- filter_paths --------------------------------------------------------


def test_filter_paths_empty_query_returns_all(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("")
    b = tmp_path / "B.txt"
    b.write_text("")
    out = filter_paths([a, b], "")
    assert sorted(p.name for p in out) == ["B.txt", "a.txt"]


def test_filter_paths_substring_case_insensitive(tmp_path):
    a = tmp_path / "Receipt.pdf"
    a.write_text("")
    b = tmp_path / "shopping.txt"
    b.write_text("")
    out = filter_paths([a, b], "receipt")
    assert [p.name for p in out] == ["Receipt.pdf"]


def test_filter_paths_no_match_returns_empty(tmp_path):
    a = tmp_path / "x.pdf"
    a.write_text("")
    assert filter_paths([a], "xyz") == []


# ---- FilePickerScreen 초기화 + ext filter -----------------------------


def test_picker_start_dir_falls_back_to_home_when_invalid(tmp_path):
    """존재하지 않는 path → home 으로 fallback."""
    sc = FilePickerScreen(start_dir=tmp_path / "nope")
    assert sc.current == Path.home().resolve()


def test_picker_start_dir_uses_provided_dir(tmp_path):
    sc = FilePickerScreen(start_dir=tmp_path)
    assert sc.current == tmp_path.resolve()


def test_picker_extensions_normalized_lowercase():
    sc = FilePickerScreen(extensions=[".PDF", ".HTML"])
    assert sc._extensions == (".pdf", ".html")


def test_picker_filter_by_ext(tmp_path):
    """`_filter_by_ext` — 디렉터리는 항상 통과, 파일은 ext 매칭만."""
    pdf = tmp_path / "x.pdf"
    pdf.write_text("")
    txt = tmp_path / "x.txt"
    txt.write_text("")
    sub = tmp_path / "subdir"
    sub.mkdir()
    sc = FilePickerScreen(extensions=[".pdf"])
    out = sc._filter_by_ext([pdf, txt, sub])
    assert sub in out
    assert pdf in out
    assert txt not in out


def test_picker_no_ext_filter_returns_all(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_text("")
    txt = tmp_path / "x.txt"
    txt.write_text("")
    sc = FilePickerScreen()
    assert sorted(p.name for p in sc._filter_by_ext([pdf, txt])) == ["x.pdf", "x.txt"]
