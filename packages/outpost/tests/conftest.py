# SPDX-License-Identifier: Apache-2.0
"""The daemon's suite runs with no secret remembered from another test, and with its own log."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
import structlog
from choregos_core.redaction import forget_secrets
from choregos_outpost import logs


@pytest.fixture(autouse=True)
def daemon_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """The daemon's own log, as JSON lines, configured exactly as `run` configures it."""
    written = io.StringIO()
    monkeypatch.setattr("sys.stdout", written)
    logs.configure("DEBUG")
    forget_secrets()
    yield written
    forget_secrets()
    structlog.reset_defaults()
