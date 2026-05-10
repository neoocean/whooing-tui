# whooing-tui monorepo — orchestrator Makefile.
#
# 두 패키지 (core/, tui/) 를 단일 venv 에 editable install + 일괄 테스트.
# 개별 패키지만 다루려면 cd core / cd tui 후 직접 pytest / pip install.
#
# Usage:
#   make install        venv + core + tui editable install + dev deps
#   make test           pytest -q (core + tui 양쪽)
#   make test-core      core 만
#   make test-tui       tui 만
#   make run            python -m whooing_tui (TUI 실행)
#   make sections       sections-list 헤드리스 호출 (.env 토큰 필요)
#   make tools          (참고) wrapper 의 도구 목록 — wrapper repo 별도
#   make clean          __pycache__ / .pytest_cache / *.egg-info 제거
#
# venv: .venv/ (monorepo-root, 단일 — core+tui 같이 활성).

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help install install-dev test test-core test-tui test-fast coverage smoke-cli run sections clean

help:
	@echo "make install     core + tui 모두 editable install + dev deps"
	@echo "make test        core + tui pytest"
	@echo "make test-core   core 만"
	@echo "make test-tui    tui 만"
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

install-dev: install

test: test-core test-tui

test-core:
	$(PYTEST) -q core/tests

test-tui:
	$(PYTEST) -q tui/tests

test-fast:
	$(PYTEST) -q --failed-first core/tests tui/tests

# tui 패키지의 라인 커버리지. core 는 자체 venv 정책이라 별도.
# pytest-cov 가 dev deps 에 포함됐는지 미설치 시 안내.
coverage:
	@$(VENV)/bin/python -c "import pytest_cov" 2>/dev/null || { \
		echo "pytest-cov 미설치 — make install (dev deps 에 포함됨)"; exit 2; }
	$(PYTEST) -q --cov=whooing_tui --cov-report=term-missing --cov-report=html tui/tests
	@echo "HTML report: htmlcov/index.html"

# 콘솔 스크립트와 python -m 둘 다 같은 결과를 주는지 빠르게 확인.
# 실 후잉 호출이 아니라 `--help` 만으로 dispatch 검증 — 토큰 불필요.
smoke-cli:
	@echo "[1/2] python -m whooing_tui --help"
	@$(PY) -m whooing_tui --help > /dev/null
	@echo "[2/2] whooing-tui --help (console_scripts entry)"
	@$(VENV)/bin/whooing-tui --help > /dev/null
	@echo "OK — 양쪽 진입점 모두 동작."

run:
	$(PY) -m whooing_tui

sections:
	$(PY) -m whooing_tui sections list

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	@echo "cleaned"
