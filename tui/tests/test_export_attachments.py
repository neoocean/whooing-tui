"""CLI subcommand `export-attachments` — entry / section 별 zip export.

CL #51146+ (A17). zip 내용물:
  - files/<file_path>  — 디스크 파일 그대로.
  - manifest.json       — schema_version + entry_id/section_id + rows 메타.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from whooing_core import attachments as core_attach
from whooing_tui import data as tui_data
from whooing_tui.cli import _build_parser, _cmd_export_attachments


def _seed_attachment(
    entry_id: str, section_id: str | None, content: bytes, filename: str,
) -> dict:
    """tmp 의 src 를 만들고 add_attachment 로 db + 디스크 정착."""
    import tempfile
    src_dir = Path(tempfile.mkdtemp())
    src = src_dir / filename
    src.write_bytes(content)
    root = tui_data.attachments_root()
    copied, sha, size = core_attach.copy_to_attachments(
        src, attachments_root=root, attach_date="2026-05-10",
    )
    rel = str(copied.relative_to(root))
    with tui_data.open_rw() as conn:
        return core_attach.upsert_attachment(
            conn, entry_id=entry_id, section_id=section_id,
            file_path=rel, original_path=str(src), original_filename=filename,
            file_size_bytes=size, file_sha256=sha,
            mime_type="application/pdf", note=None,
        )


def _run(argv: list[str]):
    parser = _build_parser()
    args = parser.parse_args(["export-attachments", *argv])
    return _cmd_export_attachments(args)


def test_export_entry_creates_zip_with_files_and_manifest(tmp_path):
    tui_data.init_shared_schema()
    _seed_attachment("e1", "s1", b"PDF1", "a.pdf")
    _seed_attachment("e1", "s1", b"PDF2", "b.pdf")
    out = tmp_path / "out.zip"
    rc = _run(["--entry", "e1", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        # 최소: manifest + 2 files.
        assert "manifest.json" in names
        assert sum(1 for n in names if n.startswith("files/")) == 2
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["entry_id"] == "e1"
        assert len(manifest["rows"]) == 2


def test_export_section_includes_all_entries(tmp_path):
    tui_data.init_shared_schema()
    _seed_attachment("e1", "s9046", b"A", "a.pdf")
    _seed_attachment("e2", "s9046", b"B", "b.pdf")
    _seed_attachment("e3", "s_other", b"C", "c.pdf")  # 다른 섹션 — 제외.
    out = tmp_path / "section.zip"
    rc = _run(["--section", "s9046", "--out", str(out)])
    assert rc == 0
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["section_id"] == "s9046"
        # s_other 의 row 미포함.
        eids = {r["entry_id"] for r in manifest["rows"]}
        assert eids == {"e1", "e2"}


def test_export_no_attachments_returns_ok_with_message(isolated, tmp_path, capsys):
    """첨부 0 → return 0 + 안내."""
    tui_data.init_shared_schema()
    out = tmp_path / "empty.zip"
    rc = _run(["--entry", "e_unknown", "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "0건" in captured.out
    # 빈 zip 도 안 만들어짐.
    assert not out.exists()


def test_export_requires_entry_or_section(tmp_path, capsys):
    tui_data.init_shared_schema()
    out = tmp_path / "x.zip"
    rc = _run(["--out", str(out)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "필수" in err


def test_export_rejects_both_entry_and_section(tmp_path, capsys):
    tui_data.init_shared_schema()
    out = tmp_path / "x.zip"
    rc = _run(["--entry", "e1", "--section", "s1", "--out", str(out)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "동시" in err


# isolated fixture — conftest 의 _isolated_user_state 가 이미 적용되지만
# 명시 isolated 가 필요한 테스트용 alias.
@pytest.fixture
def isolated():
    yield
