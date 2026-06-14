"""후잉 서버 첨부 가져오기 (server_attachments) — download + pending 판별."""

from __future__ import annotations

import respx
from httpx import Response

from whooing_tui.server_attachments import (
    ServerAttachmentError,
    download,
    pending_imports,
)

SRC = "https://static.whooing.com/get/2174def63-7ugaix3sa"


# ---- pending_imports (pure) --------------------------------------------

def _srv(uuid="u1", filename="IMG_0071.jpeg", src=SRC):
    return {"uuid": uuid, "filename": filename, "src": src,
            "mimeType": "image/jpeg", "size": 0.15}


def test_pending_excludes_already_local():
    server = [_srv(filename="a.jpg"), _srv(uuid="u2", filename="b.png")]
    local = [{"original_filename": "a.jpg"}]
    pend = pending_imports(server, local)
    assert [p["filename"] for p in pend] == ["b.png"]


def test_pending_excludes_missing_src():
    server = [_srv(filename="a.jpg", src=""), _srv(uuid="u2", filename="b.png")]
    pend = pending_imports(server, [])
    assert [p["filename"] for p in pend] == ["b.png"]


def test_pending_empty_inputs():
    assert pending_imports(None, None) == []
    assert pending_imports([], [{"original_filename": "x"}]) == []


# ---- download (respx) ---------------------------------------------------

@respx.mock
async def test_download_returns_bytes():
    respx.get(SRC).mock(return_value=Response(200, content=b"\xff\xd8\xffJPEGDATA"))
    data = await download(SRC)
    assert data == b"\xff\xd8\xffJPEGDATA"


@respx.mock
async def test_download_sends_token_when_given():
    route = respx.get(SRC).mock(return_value=Response(200, content=b"x"))
    await download(SRC, token="__tok123")
    assert route.calls.last.request.headers.get("x-api-key") == "__tok123"


@respx.mock
async def test_download_non_200_raises():
    respx.get(SRC).mock(return_value=Response(404, text="nope"))
    try:
        await download(SRC)
        assert False, "expected ServerAttachmentError"
    except ServerAttachmentError as e:
        assert "404" in str(e)
