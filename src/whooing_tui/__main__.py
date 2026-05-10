"""Entry point: `python -m whooing_tui` 또는 `whooing-tui` 콘솔 스크립트."""

from __future__ import annotations

import sys

from whooing_tui.cli import main


if __name__ == "__main__":
    sys.exit(main())
