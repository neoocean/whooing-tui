# whooing-mcp-server (mcp/) — package-local Makefile.
#
# 일반적으로 monorepo 루트의 Makefile 을 통해 호출됩니다 (`make install`,
# `make test-mcp` 등). 본 Makefile 은 mcp 패키지만 따로 다룰 때 사용.
#
# Usage:
#   make install          venv + editable install (sibling core 포함) + Chromium
#   make test             pytest -q
#   make tools            registered MCP tool 목록 출력
#   make smoke            tests/_live_smoke.py — 실 후잉 호출 (자격증명 필요)
#   make probe-s9046      tests/_probe_s9046.py — accounts/entries 구조 탐색
#   make lint             ruff check (있으면)
#   make clean            __pycache__ / .pytest_cache 제거
#
# venv: monorepo root 의 .venv 를 우선 사용. 본 디렉터리에서 직접 호출 시
# .venv/ 신설.

VENV ?= ../.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

.PHONY: help install install-dev test test-fast tools smoke probe-s9046 lint clean

help:
	@echo "make install     monorepo .venv 에 mcp 패키지 editable install"
	@echo "make test        pytest -q (모든 테스트)"
	@echo "make test-fast   pytest -q --ignore=tests/test_p4_sync.py"
	@echo "make tools       등록된 MCP 도구 목록"
	@echo "make smoke       실 후잉 호출 검증 (.env 의 WHOOING_AI_TOKEN 필요)"
	@echo "make probe-s9046 accounts/entries 구조 탐색 (.env 필요)"
	@echo "make lint        ruff check (있으면)"
	@echo "make clean       cache 디렉터리 제거"

install:
	@if [ ! -d $(VENV) ]; then \
		python3 -m venv $(VENV); \
	fi
	# whooing-core 는 sibling (../core/) — monorepo 루트 Makefile 이 먼저
	# install 하지만 이 타겟을 단독으로 실행해도 동작하도록 여기서도 처리.
	@if [ -d ../core ]; then \
		$(PIP) install --quiet -e ../core; \
	fi
	$(PIP) install --quiet -e .[dev]
	$(VENV)/bin/playwright install chromium

install-dev: install

test:
	$(PYTEST) -q

test-fast:
	$(PYTEST) -q --ignore=tests/test_p4_sync.py --ignore=tests/test_html_import.py

tools:
	$(PY) -c "from whooing_mcp.server import build_mcp; import asyncio; \
		m = build_mcp(); tools = asyncio.run(m.list_tools()); \
		print(f'{len(tools)} tools registered:'); \
		[print(f'  - {t.name}') for t in tools]"

smoke:
	$(PY) tests/_live_smoke.py

probe-s9046:
	$(PY) tests/_probe_s9046.py

lint:
	@if [ -x $(VENV)/bin/ruff ]; then \
		$(VENV)/bin/ruff check src tests; \
	else \
		echo "(ruff 미설치 — 'pip install ruff' 후 재시도)"; \
	fi

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache -o -name .mypy_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	@echo "cleaned"
