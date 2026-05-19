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


# ---- CL #52899+ : 숨김 파일 default 안 보임 + Ctrl+H 토글 ---------------


def test_safe_listdir_hides_dot_files_by_default(tmp_path):
    """`_safe_listdir` 의 default show_hidden=False — .x 로 시작하는 항목 제외."""
    from whooing_tui.screens.file_picker import _safe_listdir

    (tmp_path / "visible.txt").write_text("")
    (tmp_path / ".hidden").write_text("")
    (tmp_path / ".config").mkdir()
    (tmp_path / "Documents").mkdir()

    names = {p.name for p in _safe_listdir(tmp_path)}
    assert names == {"visible.txt", "Documents"}


def test_safe_listdir_includes_dot_files_when_show_hidden(tmp_path):
    from whooing_tui.screens.file_picker import _safe_listdir

    (tmp_path / "visible.txt").write_text("")
    (tmp_path / ".hidden").write_text("")
    names = {p.name for p in _safe_listdir(tmp_path, show_hidden=True)}
    assert names == {"visible.txt", ".hidden"}


def test_picker_starts_with_hidden_off():
    """FilePickerScreen 의 _show_hidden default = False."""
    sc = FilePickerScreen()
    assert sc._show_hidden is False


def test_picker_action_toggle_hidden_flips_flag():
    """Ctrl+H 토글 — `action_toggle_hidden` 한 번 호출 시 True."""
    sc = FilePickerScreen()
    assert sc._show_hidden is False
    # _refresh_list 는 위젯이 없어 fail 하므로 stub.
    sc._refresh_list = lambda: None  # type: ignore[method-assign]
    sc.action_toggle_hidden()
    assert sc._show_hidden is True
    sc.action_toggle_hidden()
    assert sc._show_hidden is False


# ---- CL #52929+ : 마지막 디렉토리 영구화 -------------------------------


def test_picker_uses_last_file_picker_dir_when_no_start_dir(monkeypatch, tmp_path):
    """state.json 의 last_file_picker_dir 이 있으면 그걸 시작 dir 로."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from whooing_tui.state import save_last_file_picker_dir
    target = tmp_path / "Downloads"
    target.mkdir()
    save_last_file_picker_dir(str(target))
    # start_dir 명시하지 않음 — state 에서 복원.
    sc = FilePickerScreen()
    assert sc.current == target.resolve()


def test_picker_falls_back_to_home_when_saved_dir_missing(monkeypatch, tmp_path):
    """state 에 저장된 path 가 사라졌으면 home 으로 fallback."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from whooing_tui.state import save_last_file_picker_dir
    ghost = tmp_path / "does_not_exist"
    save_last_file_picker_dir(str(ghost))
    sc = FilePickerScreen()
    assert sc.current == Path.home().resolve()


def test_picker_explicit_start_dir_overrides_saved(monkeypatch, tmp_path):
    """caller 가 명시 start_dir 주면 그게 우선."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from whooing_tui.state import save_last_file_picker_dir
    saved = tmp_path / "saved"
    saved.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    save_last_file_picker_dir(str(saved))
    sc = FilePickerScreen(start_dir=explicit)
    assert sc.current == explicit.resolve()


# ---- CL #52929+ : mouse click = highlight only -------------------------


def test_option_list_subclass_overrides_click():
    """_HighlightOnClickOptionList 가 OptionList 의 _on_click 을 override."""
    from whooing_tui.screens.file_picker import _HighlightOnClickOptionList
    from textual.widgets import OptionList
    assert issubclass(_HighlightOnClickOptionList, OptionList)
    # _on_click 메서드가 *재정의됨* (부모와 다른 객체).
    assert _HighlightOnClickOptionList._on_click is not OptionList._on_click
