"""FilePickerScreen — 디렉터리 navigation + 파일 선택 modal.

CL #51139+ (A7). 종전엔 첨부 / 명세서 import 의 파일 경로 입력이 절대 경로
텍스트만 — 사용자가 매번 path 외워야 했음. 본 picker 가:

- 시작 디렉터리 (default = `~`) 부터 트리 탐색.
- 부모 / 자식 / 형제 navigation: ←/→ (들어가/나가) + ↑/↓ (목록 이동).
- Enter: 디렉터리면 들어감, 파일이면 선택 → dismiss(path).
- 입력란에 타이핑하면 prefix 필터.
- '..' 는 항상 첫 옵션.
- Esc: 취소 → dismiss(None).

호출자는 path string 또는 None 을 받음 — 기존 `_AddPathModal` /
`_FilePathModal` 의 dismiss 형식과 동일.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option


class _HighlightOnClickOptionList(OptionList):
    """CL #52929+: mouse click 은 *highlight 만*, 실제 선택은 Enter 키 또는
    버튼 클릭에서만. 종전 OptionList 는 click 으로 `OptionSelected` 까지
    즉시 발사해 사용자가 디렉토리 들어갈 의도 없이 잘못 클릭해도 선택.

    사용자 요청: "마우스 클릭은 하이라이트로만 동작하고 실제 선택은 버튼
    클릭 또는 엔터 키를 누를 때만".
    """

    async def _on_click(self, event: events.Click) -> None:
        # Textual `OptionList._on_click` 가 highlight + action_select 양쪽
        # 수행하던 동작에서 *단일 click 은 highlight 만*. 더블 클릭은 명시적
        # 선택 (Enter 동등).
        clicked_option: int | None = event.style.meta.get("option")
        if clicked_option is not None and not self._options[clicked_option].disabled:
            self.highlighted = clicked_option
            if event.chain >= 2:
                self.action_select()
        event.stop()

log = logging.getLogger(__name__)


# 옵션 ID prefix.
_DIR_UP = "up::"     # 부모 디렉터리.
_DIR = "dir::"       # 자식 디렉터리.
_FILE = "file::"     # 파일.


def _safe_listdir(path: Path, *, show_hidden: bool = False) -> list[Path]:
    """`path` 의 자식들 — 권한/존재 오류는 빈 list. 정렬 (디렉터리 먼저).

    CL #52899+: 사용자 요청 — 숨김 파일 (`.x`) 은 default 로 표시 X.
    `show_hidden=True` 가 명시되면 (Ctrl+H 토글 등) 포함. `..` 같은
    부모/현재 dir 자체는 caller (`_refresh_list`) 가 따로 처리.
    """
    try:
        children = list(path.iterdir())
    except (PermissionError, FileNotFoundError, OSError):
        return []
    if not show_hidden:
        children = [c for c in children if not c.name.startswith(".")]
    children.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    return children


def filter_paths(
    children: Iterable[Path], query: str,
) -> list[Path]:
    """파일명 prefix / substring 필터 (대소문자 무시). 빈 query 면 그대로."""
    q = (query or "").strip().lower()
    if not q:
        return list(children)
    out: list[Path] = []
    for c in children:
        if q in c.name.lower():
            out.append(c)
    return out


class FilePickerScreen(ModalScreen[str | None]):
    """디렉터리 navigation 으로 파일 선택. dismiss(절대경로 str | None).

    `start_dir` default = `~`. `extensions` 는 lower-case suffix list (예:
    `[".pdf", ".csv", ".html"]`) — 명시되면 그 외 파일은 hidden. None 이면
    모든 파일.
    """

    BINDINGS = [
        Binding("escape", "cancel", "취소"),
        # CL #52899+: 숨김 파일 토글 (default 숨김).
        Binding("ctrl+h", "toggle_hidden", "Hidden 토글", show=True),
    ]

    DEFAULT_CSS = """
    FilePickerScreen {
        align: center middle;
    }
    #fp_box {
        background: $panel;
        border: thick $primary;
        padding: 1;
        width: 95%;
        max-width: 90;
        min-width: 40;
        height: 30;
    }
    #fp_path {
        height: 1;
        color: $accent;
    }
    #fp_filter {
        margin-top: 1;
    }
    #fp_list {
        height: 1fr;
        margin-top: 1;
    }
    #fp_hint {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        *,
        start_dir: str | Path | None = None,
        extensions: list[str] | None = None,
        title: str = "파일 선택",
    ) -> None:
        super().__init__()
        self._title = title
        self._extensions = (
            tuple(e.lower() for e in extensions) if extensions else None
        )
        # start_dir 정규화.
        # CL #52929+: caller 미지정 시 *마지막에 사용했던 디렉토리* 를 복원
        # (state.json). 처음 사용이거나 해당 path 가 사라졌으면 home 으로
        # fallback.
        sd: Path | None = None
        if start_dir:
            sd = Path(start_dir).expanduser()
        else:
            try:
                from whooing_tui.state import load_last_file_picker_dir
                last = load_last_file_picker_dir()
                if last:
                    sd = Path(last).expanduser()
            except Exception:  # pragma: no cover — state.json 로드 실패는 silent.
                log.debug("load_last_file_picker_dir failed", exc_info=True)
        if sd is None or not sd.is_dir():
            sd = Path.home()
        self.current: Path = sd.resolve()
        self._all_children: list[Path] = []
        # CL #52899+: 숨김 파일 표시 여부 — default 숨김. Ctrl+H 토글.
        self._show_hidden: bool = False

    def compose(self) -> ComposeResult:
        with Container(id="fp_box"):
            yield Label(f"[bold]{self._title}[/bold]")
            yield Static(str(self.current), id="fp_path")
            yield Input(placeholder="필터 (파일명 부분 일치)", id="fp_filter")
            yield _HighlightOnClickOptionList(id="fp_list")
            yield Static(
                "Enter / 더블클릭=선택 · 클릭=하이라이트만 · "
                "←=부모 / →=자식 / Ctrl+H=숨김 / Esc=취소",
                id="fp_hint",
            )

    def on_mount(self) -> None:
        self._refresh_list()
        try:
            self.query_one("#fp_list", OptionList).focus()
        except Exception:  # pragma: no cover
            pass

    # ---- list rendering --------------------------------------------------

    def _filter_by_ext(self, paths: list[Path]) -> list[Path]:
        if self._extensions is None:
            return paths
        out = []
        for p in paths:
            if p.is_dir():
                out.append(p)
            elif p.suffix.lower() in self._extensions:
                out.append(p)
        return out

    def _refresh_list(self) -> None:
        opt = self.query_one("#fp_list", OptionList)
        opt.clear_options()
        # path label 갱신.
        self.query_one("#fp_path", Static).update(str(self.current))

        children = self._filter_by_ext(
            _safe_listdir(self.current, show_hidden=self._show_hidden),
        )
        query = self.query_one("#fp_filter", Input).value.strip()
        children = filter_paths(children, query)
        self._all_children = children

        # 부모 (..) 항상 첫 옵션 — 단 root 이면 disabled.
        is_root = self.current.parent == self.current
        opt.add_option(Option(
            "[dim]..  (부모 디렉터리)[/dim]",
            id=f"{_DIR_UP}{self.current.parent}",
            disabled=is_root,
        ))
        # 디렉터리 / 파일 표시.
        for c in children:
            if c.is_dir():
                opt.add_option(Option(
                    f"📁 {c.name}/",
                    id=f"{_DIR}{c}",
                ))
            else:
                # 파일 — size hint.
                try:
                    size = c.stat().st_size
                    size_label = _fmt_bytes(size)
                except OSError:
                    size_label = ""
                opt.add_option(Option(
                    f"   {c.name}  [dim]({size_label})[/dim]",
                    id=f"{_FILE}{c}",
                ))

        # cursor 를 첫 selectable 옵션으로.
        for i in range(opt.option_count):
            o = opt.get_option_at_index(i)
            if not o.disabled:
                opt.highlighted = i
                break

    # ---- events ----------------------------------------------------------

    @on(Input.Changed, "#fp_filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        self._refresh_list()

    @on(Input.Submitted, "#fp_filter")
    def _on_filter_submitted(self, event: Input.Submitted) -> None:
        opt = self.query_one("#fp_list", OptionList)
        idx = opt.highlighted
        if idx is None:
            return
        try:
            option = opt.get_option_at_index(idx)
        except Exception:  # pragma: no cover
            return
        if option.disabled or not option.id:
            return
        self._activate(option.id)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        oid = event.option.id
        if oid:
            self._activate(oid)

    def _activate(self, oid: str) -> None:
        """옵션 선택 — 디렉터리면 들어감, 파일이면 dismiss 로 path 반환."""
        if oid.startswith(_DIR_UP):
            target = Path(oid[len(_DIR_UP):])
            self.current = target.resolve()
            self.query_one("#fp_filter", Input).value = ""
            self._refresh_list()
        elif oid.startswith(_DIR):
            target = Path(oid[len(_DIR):])
            self.current = target.resolve()
            self.query_one("#fp_filter", Input).value = ""
            self._refresh_list()
        elif oid.startswith(_FILE):
            # CL #52929+: 다음 호출 시 같은 디렉토리에서 시작하도록 저장.
            try:
                from whooing_tui.state import save_last_file_picker_dir
                save_last_file_picker_dir(str(self.current))
            except Exception:  # pragma: no cover — state.json 저장 실패 silent.
                log.debug("save_last_file_picker_dir failed", exc_info=True)
            self.dismiss(oid[len(_FILE):])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_hidden(self) -> None:
        """Ctrl+H — 숨김 파일 표시 토글 (CL #52899+)."""
        self._show_hidden = not self._show_hidden
        self._refresh_list()


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f}K"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}M"
    return f"{n / (1024 ** 3):.2f}G"
