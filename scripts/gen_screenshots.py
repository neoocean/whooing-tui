#!/usr/bin/env python3
"""gen_screenshots.py — docs/MANUAL.md 용 실제 화면 SVG 스크린샷 생성기.

방식 ① **Textual SVG export** — pytmux(`scripts/gen_screenshots.py`) /
docker-monitor(`scripts/render-terminal-svg.py`) 의 매뉴얼 스크린샷 방식을
참고. 헤드리스(`App.run_test`)로 FakeClient(결정적·PII 없는 샘플 데이터)를
운전해 각 화면을 클라가 실제로 그리는 그대로 SVG 로 떠 `docs/image/` 에
저장한다.

저장 SVG 후처리(_redact_svg):
  - PII 마스킹(이메일 → user@example.com) — 공개 GitHub 미러 보호 차원의
    방어. 본 생성기는 가짜 데이터만 쓰지만 동일 정책 적용.
  - 한글 등 와이드 문자 자간 보정(_fix_cjk_textlength) — Rich export_svg 의
    textLength 버그(글자수 기준 계산) 교정. pytmux 와 동일.

사용:  python3 scripts/gen_screenshots.py        # 전체 생성
       python3 scripts/gen_screenshots.py edit   # 이름에 'edit' 포함 장면만
"""

from __future__ import annotations

import asyncio
import html as _html
import os
import pathlib
import re as _re
import sys
import tempfile

# --- sys.path + 격리 환경 (실 사용자 db/토큰 미접촉) ---------------------
_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tui" / "src"))
sys.path.insert(0, str(_ROOT / "core" / "src"))
_TMP = tempfile.mkdtemp(prefix="whooing-shots-")
os.environ.setdefault("WHOOING_DATA_DIR", os.path.join(_TMP, "data"))
os.environ.setdefault("WHOOING_ATTACHMENTS_DIR", os.path.join(_TMP, "attach"))
os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(_TMP, "xdg"))
os.environ.setdefault("WHOOING_AI_TOKEN", "dummy-token-for-screenshots")

from rich.cells import cell_len as _cell_len  # noqa: E402

from whooing_tui.app import WhooingTuiApp  # noqa: E402
from whooing_tui.screens.entries import EntriesScreen  # noqa: E402
from whooing_tui.screens.revision_history import RevisionHistoryScreen  # noqa: E402
from whooing_tui.screens.trash import TrashScreen  # noqa: E402
from whooing_tui.widgets.menubar import MenuPopup  # noqa: E402

OUT_DIR = str(_ROOT / "docs" / "image")
SIZE = (98, 30)


# ---- FakeClient (결정적 샘플) ------------------------------------------

class FakeClient:
    def __init__(self):
        self.sections = [{"section_id": "s1", "title": "가계부"}]
        self.accounts = {
            "expenses": [
                {"account_id": "x50", "title": "식비", "type": "expenses"},
                {"account_id": "x51", "title": "교통", "type": "expenses"},
                {"account_id": "x120", "title": "공부", "type": "expenses"},
            ],
            "assets": [{"account_id": "x11", "title": "현금", "type": "assets"}],
            "liabilities": [
                {"account_id": "x80", "title": "하나카드", "type": "liabilities"},
                {"account_id": "x153", "title": "현대카드", "type": "liabilities"},
            ],
        }
        self.entries = {"s1": _sample_entries()}

    async def list_sections(self):
        return list(self.sections)

    async def list_accounts(self, section_id):
        return self.accounts

    async def list_entries(self, section_id, start_date, end_date):
        return list(self.entries.get(section_id, []))


def _sample_entries():
    rows = [
        ("e1", "20260607", 20000, "x50", "x80", "저녁(닭칼국수)"),
        ("e2", "20260607", 8000, "x50", "x80", "커피(타이거수지)"),
        ("e3", "20260606", 327000, "x120", "x153", "Claude Max x20"),
        ("e4", "20260605", 68000, "x120", "x153", "Hetzner"),
        ("e5", "20260605", 30000, "x51", "x153", "교통비(모바일티머니)"),
        ("e6", "20260603", 20500, "x50", "x80", "저녁(써브웨이)"),
        ("e7", "20260603", 16000, "x50", "x80", "점심(공릉동멸치국수)"),
    ]
    out = []
    for eid, date, money, left, right, item in rows:
        out.append({
            "entry_id": eid, "entry_date": date, "money": money,
            "l_account": "expenses", "l_account_id": left,
            "r_account": "liabilities", "r_account_id": right,
            "item": item, "memo": "",
        })
    return out


# ---- SVG 후처리 (pytmux gen_screenshots 의 _redact_svg 참고) ------------

_EMAIL_RE = _re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TEXT_RE = _re.compile(
    r'(<text\b[^>]*?textLength=")([0-9.]+)("[^>]*>)([^<]*)(</text>)'
)


def _fix_cjk_textlength(svg: str) -> str:
    """와이드 문자가 든 <text> 의 textLength 를 셀폭 기준으로 보정 (Rich
    export_svg 가 textLength 를 글자수로 계산하는 버그 교정)."""
    def repl(m):
        pre, length, mid, content, end = m.groups()
        text = _html.unescape(content)
        n = len(text)
        cells = _cell_len(text)
        if n == 0 or cells == 0 or cells == n:
            return m.group(0)
        char_width = float(length) / n
        return f"{pre}{char_width * cells:g}{mid}{content}{end}"
    return _TEXT_RE.sub(repl, svg)


def _redact_svg(svg: str) -> str:
    return _fix_cjk_textlength(_EMAIL_RE.sub("user@example.com", svg))


# ---- 장면 운전 함수 ----------------------------------------------------

async def _boot(app, pilot):
    for _ in range(120):
        if isinstance(app.screen, EntriesScreen) and app.session.section_id:
            tbl = app.screen.query("#entries-table")
            if tbl and tbl.first().row_count >= 1:
                break
        await pilot.pause(0.05)
    await pilot.pause(0.2)
    return app.screen


async def scene_entries(app, pilot):
    await _boot(app, pilot)


async def scene_context_menu(app, pilot):
    es = await _boot(app, pilot)
    es.action_show_context_menu()
    await pilot.pause(0.3)


async def scene_edit_dialog(app, pilot):
    es = await _boot(app, pilot)
    es.action_edit_entry()
    await pilot.pause(0.3)


async def scene_screen_menu(app, pilot):
    es = await _boot(app, pilot)
    menus = es._build_menus()
    # '화면' 메뉴 (휴지통 항목 포함) 찾기.
    idx = next((i for i, m in enumerate(menus) if m.name == "화면"), 0)
    app.push_screen(MenuPopup(menus[idx], menus=menus, menu_index=idx))
    await pilot.pause(0.3)


async def scene_trash(app, pilot):
    await _boot(app, pilot)
    deleted = [
        {"logical_id": "e9", "entry_date": "20260604", "money": 12000,
         "item": "점심(온유파스타)", "deleted_at": "2026-06-07T21:40"},
        {"logical_id": "e8", "entry_date": "20260602", "money": 4500,
         "item": "간식(편의점)", "deleted_at": "2026-06-06T11:02"},
    ]
    app.push_screen(TrashScreen(deleted))
    await pilot.pause(0.3)


async def scene_revision_history(app, pilot):
    await _boot(app, pilot)
    revs = [
        {"revision_no": 1, "op": "create", "created_at": "2026-06-04T12:10",
         "money": 30000, "item": "저녁", "memo": "",
         "l_account": "expenses", "l_account_id": "x50",
         "r_account": "liabilities", "r_account_id": "x80",
         "entry_date": "20260604"},
        {"revision_no": 2, "op": "edit", "created_at": "2026-06-05T19:40",
         "money": 30000, "item": "저녁(닭칼국수)", "memo": "",
         "note": "item '저녁'→'저녁(닭칼국수)'",
         "l_account": "expenses", "l_account_id": "x50",
         "r_account": "liabilities", "r_account_id": "x80",
         "entry_date": "20260604"},
        {"revision_no": 3, "op": "delete", "created_at": "2026-06-06T10:02",
         "money": 30000, "item": "저녁(닭칼국수)", "memo": "",
         "l_account": "expenses", "l_account_id": "x50",
         "r_account": "liabilities", "r_account_id": "x80",
         "entry_date": "20260604"},
        {"revision_no": 4, "op": "restore", "created_at": "2026-06-07T21:53",
         "money": 27000, "item": "저녁(닭칼국수)", "memo": "",
         "note": "money 30,000→27,000",
         "l_account": "expenses", "l_account_id": "x50",
         "r_account": "liabilities", "r_account_id": "x80",
         "entry_date": "20260604"},
    ]
    app.push_screen(RevisionHistoryScreen(revs, logical_id="e1"))
    await pilot.pause(0.3)


# (name, desc, drive[, size]) — size 생략 시 기본 SIZE.
SCENES = [
    ("01-entries", "거래 목록 (메인 화면)", scene_entries),
    ("02-context-menu", "거래 컨텍스트 메뉴 (m)", scene_context_menu),
    # 편집 폼은 세로가 길어 전체가 보이도록 더 큰 캔버스로 렌더.
    ("03-edit-dialog", "거래 수정 폼 (e)", scene_edit_dialog, (98, 40)),
    ("04-screen-menu", "화면 메뉴 — 휴지통 진입", scene_screen_menu),
    ("05-revision-history", "수정 이력 + 되돌리기 (H)", scene_revision_history),
    ("06-trash", "휴지통 — 삭제 거래 복원", scene_trash),
]


async def _shoot(name, desc, drive, size=SIZE):
    fake = FakeClient()
    app = WhooingTuiApp(client=fake)  # type: ignore[arg-type]
    async with app.run_test(size=size) as pilot:
        await drive(app, pilot)
        app.refresh()
        await pilot.pause(0.3)
        svg = app.export_screenshot(title=f"whooing-tui — {desc}")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name + ".svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_redact_svg(svg))
    print(f"  ✓ {name}.svg — {desc}")


async def _main(filt=None):
    todo = [s for s in SCENES if not filt or filt in s[0]]
    if not todo:
        print(f"매칭 장면 없음: {filt!r}. 사용 가능: "
              + ", ".join(s[0] for s in SCENES))
        return 1
    print(f"스크린샷 생성 → {OUT_DIR}")
    for scene in todo:
        name, desc, drive = scene[0], scene[1], scene[2]
        size = scene[3] if len(scene) > 3 else SIZE
        try:
            await _shoot(name, desc, drive, size)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(asyncio.run(_main(arg)))
