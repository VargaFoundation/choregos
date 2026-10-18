.PHONY: test lint

# The interpreter that runs the tests: `python3` when it has pytest, otherwise a `.venv` made on
# first use. A Choregos runner's `python3` is its own, without pytest (S37-14).
PYTHON := $(shell python3 -c "import pytest" >/dev/null 2>&1 && echo python3 || echo .venv/bin/python)

test: $(filter .venv/bin/python,$(PYTHON))
	$(PYTHON) -m pytest -q

lint:
	python3 -m compileall -q src tests

.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/python -m pip install -q pytest==8.3.5
