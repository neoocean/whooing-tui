#!/usr/bin/env python3
"""whooing — monorepo 루트 진입점.

`python whooing.py [args]` 가 `python -m whooing_tui [args]` 와 동등.

사용:
    python whooing.py                            Textual TUI 실행
    python whooing.py sections list              헤드리스 CLI
    python whooing.py accounts list --section s1
    python whooing.py entries list --days 7
    python whooing.py --help

동작:
    본 스크립트는 외부 deps (httpx / textual / pydantic / python-dotenv)
    가 필요하다. 시스템 python 으로 실행되면 자동으로 monorepo 의
    `.venv/bin/python` 으로 re-exec — 사용자가 venv 를 명시 활성화하지
    않아도 `python3 whooing.py` 만으로 동작한다.

    `.venv/` 가 없으면 stderr 안내 + exit 3 — 먼저 monorepo 루트에서
    `make install` 을 실행하라는 메시지.

전제:
    monorepo 루트에서 `make install` 으로 venv + 모든 패키지 editable
    install 완료. 시스템 python 에 deps 가 따로 설치돼 있어도 동작 (그
    경우 re-exec 없이 그대로).
"""

from __future__ import annotations

import os
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PY = os.path.join(_HERE, ".venv", "bin", "python")


def _running_in_venv() -> bool:
    """현재 인터프리터가 monorepo 의 `.venv` 안에서 동작 중인지.

    `sys.executable` 의 realpath 비교는 venv 의 python 이 시스템 python
    의 symlink 인 macOS / Linux 환경에서 양쪽이 같은 binary 로 풀려버려
    구분이 안 된다. 표준 venv 마커인 `sys.prefix` (venv 활성화 시 venv
    root 를 가리킴) 를 우리 `.venv` 디렉토리와 비교 — venv 모듈이 만든
    `pyvenv.cfg` 가 설정하는 값이라 신뢰 가능.
    """
    venv_dir = os.path.join(_HERE, ".venv")
    if not os.path.isdir(venv_dir):
        return False
    try:
        return os.path.realpath(sys.prefix) == os.path.realpath(venv_dir)
    except OSError:
        return False


def _can_import_deps() -> bool:
    """필수 외부 deps 가 현재 인터프리터에서 import 가능한지 빠르게 확인.

    re-exec 가 가능한 환경에서도 사용자가 의도적으로 시스템 python 에
    deps 를 설치한 경우 (예: pipx 또는 시스템 패키지) 그대로 동작하게
    하기 위함.
    """
    try:
        import httpx  # noqa: F401
        import textual  # noqa: F401
        import pydantic  # noqa: F401
        import dotenv  # noqa: F401
        return True
    except ImportError:
        return False


def _reexec_in_venv_if_needed() -> None:
    """필요하면 monorepo `.venv/bin/python` 으로 자기 자신을 re-exec.

    조건:
      - 이미 venv python 이면 skip.
      - 현재 인터프리터에서 deps 가 다 import 되면 skip (시스템 python +
        외부 deps 설치 케이스).
      - venv python 이 존재하면 re-exec.
      - 그 외 (venv 도 없고 deps 도 없음) → stderr 안내 + exit 3.
    """
    if _running_in_venv():
        return
    if _can_import_deps():
        return
    if os.path.isfile(_VENV_PY) and os.access(_VENV_PY, os.X_OK):
        # os.execv 는 현재 프로세스를 새 인터프리터로 교체. 이후 라인은
        # 실행되지 않는다 — 새 인터프리터가 본 파일을 처음부터 다시 실행.
        os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])
    else:
        # venv 도 없고 deps 도 없음.
        print(
            "error: 외부 deps (httpx / textual / pydantic / python-dotenv) 가 "
            "없고 .venv/ 도 보이지 않습니다.\n"
            "       monorepo 루트에서 먼저 `make install` 을 실행하세요. "
            "그러면 .venv/ 가 만들어지고 본 스크립트는 자동으로 .venv 의 "
            "python 으로 동작합니다.",
            file=sys.stderr,
        )
        sys.exit(3)


def _ensure_in_tree_paths() -> None:
    """monorepo 의 in-tree 패키지를 sys.path 에 prepend.

    venv editable install 이 정상이라면 무해 (같은 디렉토리). 시스템
    python + 외부 deps 케이스에서도 본 패키지 자체는 sys.path 로 발견.
    """
    for sub in ("tui/src", "core/src"):
        p = os.path.join(_HERE, sub)
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    _reexec_in_venv_if_needed()  # 필요시 새 인터프리터로 교체 (이후 도달 X)
    _ensure_in_tree_paths()
    # 지연 import — sys.path 조정 *후* 에 import 해야 한다.
    from whooing_tui.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
