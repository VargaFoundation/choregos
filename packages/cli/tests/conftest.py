"""CLI fixtures: no test meets the version check (S62-08) unless it asks for it."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_version_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check reads `GET /edition` once a process and writes under ~/.cache: the suites mock
    # their own routes and must not write there. `test_self_update.py` turns it back on.
    monkeypatch.setenv("CHOREGOS_NO_VERSION_CHECK", "1")
