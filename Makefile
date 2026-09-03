# Developer entry points. Every target is what the matching CI job runs, so `make lint test`
# locally is a faithful preview of the first CI run.
#
#   make lint            ruff check + ruff format --check + mypy
#   make test-unit       pure-Python tests, no browser (< 5 s)
#   make test-integration browser tests against the local fixture pages (~2 min)
#   make test-e2e        real MCP stdio server + real Chromium (~1 min)
#   make test            all three tiers
#   make coverage        unit + integration with coverage (terminal + coverage.xml)
#   make bench-smoke     <= 2 min bench subset, results in bench/results/smoke/ (never the published set)
#
# PY selects the interpreter; default is the project venv if it exists, else whatever `python` is.
PY ?= $(shell test -x $(HOME)/.venvs/action-receipt/bin/python && echo $(HOME)/.venvs/action-receipt/bin/python || echo python)
PYTEST ?= $(PY) -m pytest
PYTEST_ARGS ?= -q -p no:cacheprovider
JUNIT_DIR ?= junit

.PHONY: help install lint format typecheck test-unit test-integration test-e2e test coverage bench-smoke build clean

help:
	@grep -E '^#   make' Makefile | sed 's/^#   //'

install:
	$(PY) -m pip install -e '.[dev]'
	$(PY) -m playwright install chromium

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(MAKE) typecheck

format:
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .

typecheck:
	$(PY) -m mypy

test-unit:
	$(PYTEST) $(PYTEST_ARGS) -m unit tests/unit

test-integration:
	@mkdir -p $(JUNIT_DIR)
	$(PYTEST) $(PYTEST_ARGS) -m integration --junitxml=$(JUNIT_DIR)/integration.xml tests

test-e2e:
	@mkdir -p $(JUNIT_DIR)
	$(PYTEST) $(PYTEST_ARGS) -m e2e --junitxml=$(JUNIT_DIR)/e2e.xml tests

test:
	@mkdir -p $(JUNIT_DIR)
	$(PYTEST) $(PYTEST_ARGS) --junitxml=$(JUNIT_DIR)/all.xml tests

coverage:
	$(PYTEST) $(PYTEST_ARGS) -m "unit or integration" --cov=action_receipt --cov-branch \
		--cov-report=term-missing --cov-report=xml:coverage.xml tests

# The smoke subset: overhead n=5, settlement 2 trials x 2 delays, accuracy 10 cases x 1 repeat.
# Deterministic, local fixtures only, no Ollama / no network. Writes to bench/results/smoke/ so the
# published bench/results/*.md are never overwritten by a smoke run.
bench-smoke:
	AR_BENCH_RESULTS=bench/results/smoke bench/smoke.sh

build:
	rm -rf dist build
	$(PY) -m build

clean:
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml $(JUNIT_DIR) htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
