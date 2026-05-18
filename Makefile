# whooing-tui monorepo — orchestrator Makefile.
#
# 두 패키지 (core/, tui/) 를 단일 venv 에 editable install + 일괄 테스트.
# 개별 패키지만 다루려면 cd <pkg> 후 직접 pytest / pip install.
#
# CL #52846+: mcp/ 패키지 완전 제거 — archived 2026-05-10 이후 본 코드베이스
# 에서 사용 없음. 복구가 필요하면 P4 history 의 #52845 이전으로 sync.
#
# Usage:
#   make install        venv + core + tui editable install + dev deps
#   make test           pytest -q (2 패키지 전부)
#   make test-core      core 만
#   make test-tui       tui 만
#   make run            python -m whooing_tui (TUI 실행)
#   make sections       sections-list 헤드리스 호출 (.env 토큰 필요)
#   make clean          __pycache__ / .pytest_cache / *.egg-info 제거
#
# venv: .venv/ (monorepo-root, 단일 — 2 패키지 같이 활성).

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help install install-dev test test-core test-tui test-fast \
	coverage smoke-cli run sections clean

help:
	@echo "make install     core + tui 모두 editable install + dev deps"
	@echo "make test        2 패키지 pytest"
	@echo "make test-core   core 만 (어댑터 / db / attachments)"
	@echo "make test-tui    tui 만 (Textual 화면)"
	@echo "make coverage    tui 의 pytest --cov (HTML report → htmlcov/)"
	@echo "make smoke-cli   whooing-tui 콘솔 스크립트 + python -m 둘 다 동작 확인"
	@echo "make run         Textual TUI 실행"
	@echo "make sections    sections-list 헤드리스 호출"
	@echo "make clean       cache 디렉터리 제거"

install:
	@if [ ! -d $(VENV) ]; then \
		python3 -m venv $(VENV); \
	fi
	$(PIP) install --quiet -e 'core[dev]'
	$(PIP) install --quiet -e 'tui[dev]'
	@# CL #52841+: Playwright 헤드리스 chromium 자동 설치. HTML 명세서
	@# 어댑터 (현대카드 / 하나카드) 가 client-side JS 복호화 처리에 사용.
	@# 이미 설치된 환경에서는 idempotent (별도 다운로드 안 함). 네트워크
	@# 부재 / 권한 문제 시 silent skip — 사용자가 직접 `playwright install
	@# chromium` 실행 가능. 사이즈 ~170MB.
	@$(VENV)/bin/playwright install chromium >/dev/null 2>&1 \
		&& echo "OK — chromium (Playwright) 준비됨." \
		|| echo "WARN — playwright install chromium 자동 실행 실패 (HTML 명세서 import 시 다시 시도하세요)."
	@# CL #52846+: 이전 환경에 남은 whooing-mcp editable install 정리 — 신규
	@# 호스트에서는 no-op. 실패해도 silent (이미 없으면 정상). 패키지 이름은
	@# `whooing-mcp-server` (mcp/pyproject.toml 의 name 필드).
	@$(PIP) uninstall -y -q whooing-mcp-server >/dev/null 2>&1 || true
	@echo "OK — core / tui editable install."

install-dev: install

test: test-core test-tui

test-core:
	$(PYTEST) -q core/tests

test-tui:
	$(PYTEST) -q tui/tests

test-fast:
	$(PYTEST) -q --failed-first core/tests tui/tests

# tui 패키지의 라인 커버리지. core 는 자체 분리 정책이라 별도.
coverage:
	@$(VENV)/bin/python -c "import pytest_cov" 2>/dev/null || { \
		echo "pytest-cov 미설치 — make install (dev deps 에 포함됨)"; exit 2; }
	$(PYTEST) -q --cov=whooing_tui --cov-report=term-missing --cov-report=html tui/tests
	@echo "HTML report: htmlcov/index.html"

# 모든 진입점이 같은 결과를 주는지 빠르게 확인 — `python -m`, console
# 스크립트, monorepo 루트의 whooing.py 셋 다.
smoke-cli:
	@echo "[1/3] python -m whooing_tui --help"
	@$(PY) -m whooing_tui --help > /dev/null
	@echo "[2/3] whooing-tui --help (console_scripts entry)"
	@$(VENV)/bin/whooing-tui --help > /dev/null
	@echo "[3/3] python whooing.py --help (monorepo 루트 진입점)"
	@$(PY) whooing.py --help > /dev/null
	@echo "OK — 진입점 3 종 모두 동작."

run:
	$(PY) -m whooing_tui

sections:
	$(PY) -m whooing_tui sections list

# CL #51132+ (A2): 후잉에 없는 entry_id 의 첨부 row + 디스크 파일 정리.
# 안전을 위해 default 는 dry-run — 실 삭제는 `make gc-attachments-go`.
gc-attachments:
	$(PY) -m whooing_tui gc-attachments --dry-run

gc-attachments-go:
	$(PY) -m whooing_tui gc-attachments

# CL #51146+ (A17): 첨부 export — 사용자가 SECTION / OUT 변수 지정.
# 예: make export-attachments SECTION=s9046 OUT=/tmp/whooing-attach.zip
SECTION ?=
ENTRY ?=
OUT ?= /tmp/whooing-attachments.zip
export-attachments:
	@if [ -n "$(ENTRY)" ]; then \
		$(PY) -m whooing_tui export-attachments --entry $(ENTRY) --out $(OUT); \
	elif [ -n "$(SECTION)" ]; then \
		$(PY) -m whooing_tui export-attachments --section $(SECTION) --out $(OUT); \
	else \
		echo "Usage: make export-attachments SECTION=s9046 OUT=/tmp/x.zip"; \
		echo "       make export-attachments ENTRY=e1234 OUT=/tmp/x.zip"; \
	fi

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	@echo "cleaned"
