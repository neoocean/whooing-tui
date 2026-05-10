#!/usr/bin/env python3
"""whooing — monorepo 루트 진입점.

`python whooing.py [args]` 가 `python -m whooing_tui [args]` 와 동등.

사용:
    python whooing.py                            Textual TUI 실행
    python whooing.py sections list              헤드리스 CLI
    python whooing.py accounts list --section s1
    python whooing.py entries list --days 7
    python whooing.py --help

전제:
    monorepo 루트에서 `make install` 후. `.venv/bin/python whooing.py`
    또는 venv 활성화 후 `python whooing.py`. 시스템 python 도 외부 deps
    (httpx / textual / pydantic / python-dotenv) 가 설치돼 있으면 동작.

본 파일은 `tui/src` (그리고 호환을 위해 `core/src`) 를 sys.path 에 prepend
한 뒤 `whooing_tui.cli.main()` 으로 그대로 위임 — 자체 로직 없음. 같은
프로세스에서 `whooing-tui` 콘솔 스크립트와도 100% 동등하게 동작 (코드
경로 재사용).
"""

from __future__ import annotations

import os
import sys


def _ensure_in_tree_paths() -> None:
    """monorepo 의 in-tree 패키지를 sys.path 에 prepend.

    `make install` 의 editable install 이 정상이라면 무해 (같은 디렉토리
    를 가리킴). venv 활성화가 안 된 상태에서도 본 패키지는 import 가능
    — 단 외부 deps 가 시스템 python 에 있어야.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for sub in ("tui/src", "core/src"):
        p = os.path.join(here, sub)
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    _ensure_in_tree_paths()
    # 지연 import — sys.path 조정 *후* 에 import 해야 한다.
    from whooing_tui.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
