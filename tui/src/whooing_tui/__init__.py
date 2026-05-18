"""whooing-tui — Textual TUI for Whooing 가계부.

후잉(whooing.com) 의 섹션 / 계정과목 / 거래내역을 터미널에서 빠르게
조회·입력하기 위한 도구. 핵심 라이브러리(REST 클라이언트·인증·날짜·에러
매핑)는 본래 같은 워크스페이스의 `whooing-mcp-server-wrapper` 와 공유
하도록 만들어졌으나, **wrapper 프로젝트는 2026-05-10 종료 (archived)** —
관련 코드는 monorepo 의 `mcp/` 에 archive 형태로 보존.
"""

__version__ = "0.60.1"
