.PHONY: test lint typecheck

# The interpreter that runs the checks: `python3` when it has pytest and mypy, otherwise a `.venv`
# made on first use. A Choregos runner's `python3` is its own, without them (S37-14). The three
# targets are the runner's Python checks (`make test`, `make lint`, `make typecheck`, S37-14b).
PYTHON := $(shell python3 -c "import pytest, mypy" >/dev/null 2>&1 && echo python3 || echo .venv/bin/python)
VENV := $(filter .venv/bin/python,$(PYTHON))

test: $(VENV)
	$(PYTHON) -m pytest -q

lint:
	python3 -m compileall -q src tests

typecheck: $(VENV)
	$(PYTHON) -m mypy

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/python -m pip install -q pytest==8.3.5 mypy==1.15.0
