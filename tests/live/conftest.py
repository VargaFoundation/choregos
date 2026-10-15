"""\"Live\" tests: the adapters against the real services.

They only run if the credentials are in the environment — never in the repository
(AGENTS.md: no hard-coded secret). Without them they are **skipped**, not green: a test that
does not run proves nothing, and saying so is half the work.

    CHOREGOS_LIVE_GITLAB_TOKEN=… CHOREGOS_LIVE_GITLAB_PROJECT=group/project \\
    uv run pytest tests/live -m live
"""

from __future__ import annotations

import os

import pytest


def require(*names: str) -> dict[str, str]:
    """Return the requested variables, or skip the test saying which ones are missing."""
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"live test skipped — variables not set: {', '.join(missing)}")
    return values
