# whooing-tui — common operator targets.
#
# Usage:
#   make install       venv + editable install + dev deps
#   make test          pytest -q
#   make run           python -m whooing_tui (TUI 실행)
#   make sections      sections-list 헤드리스 호출 (.env 토큰 필요)
#   make clean         __pycache__ / .pytest_cache 제거
#
# venv: .venv/ (project-local).

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help install install-dev test test-fast run sections clean

help:
	@echo "make install   venv + editable install + dev deps"
	@echo "make test      pytest -q (모든 테스트)"
	@echo "make run       Textual TUI 실행"
	@echo "make sections  sections-list 헤드리스 호출 (.env 토큰 필요)"
	@echo "make clean     cache 디렉터리 제거"

install:
	@if [ ! -d $(VENV) ]; then \
		python3 -m venv $(VENV); \
	fi
	$(PIP) install --quiet -e .[dev]

install-dev: install

test:
	$(PYTEST) -q

test-fast:
	$(PYTEST) -q --failed-first

run:
	$(PY) -m whooing_tui

sections:
	$(PY) -m whooing_tui sections list

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	@echo "cleaned"
