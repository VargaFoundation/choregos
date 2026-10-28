"""\"Live\" tests: the adapters against the real services.

They only run if the credentials are in the environment — never in the repository
(AGENTS.md: no hard-coded secret). Without them they are **skipped**, not green: a test that
does not run proves nothing, and saying so is half the work.

A job that is meant to test a service cannot let a vanished secret turn into a skip. The
`CHOREGOS_LIVE_REQUIRED` variable lists those services (comma-separated, case-insensitive: `gitlab`,
`jira`, `litellm`, or `all`): when a variable of a listed service is missing, `require()` **fails**
instead of skipping. Only the *missing* variables decide: an optional companion of a required service
(the realtime model next to LiteLLM) still skips its own test.

    CHOREGOS_LIVE_GITLAB_TOKEN=… CHOREGOS_LIVE_GITLAB_PROJECT=group/project \\
    uv run pytest tests/live -m live

    # red, not skipped, when a GitLab variable is missing:
    CHOREGOS_LIVE_REQUIRED=gitlab uv run pytest tests/live -m live
"""

from __future__ import annotations

import os

import pytest


def required_services() -> set[str]:
    """The services whose live tests must run, from `CHOREGOS_LIVE_REQUIRED` (`all` is allowed)."""
    raw = os.environ.get("CHOREGOS_LIVE_REQUIRED", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def service_of(name: str) -> str:
    """The service of a `CHOREGOS_LIVE_<SERVICE>_*` variable: `CHOREGOS_LIVE_GITLAB_TOKEN` -> `gitlab`."""
    parts = name.lower().split("_")
    return parts[2] if len(parts) > 2 else ""


def require(*names: str) -> dict[str, str]:
    """Return the requested variables, or say which ones are missing: the test is skipped, unless a
    missing variable belongs to a service that `CHOREGOS_LIVE_REQUIRED` names, and then it fails."""
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        wanted = required_services()
        # Only the missing variables decide: an optional companion that is unset must not turn
        # the run of a required service red, and a set one must not hide a missing secret.
        services = {service_of(name) for name in missing}
        needed = sorted(services if "all" in wanted else services & wanted)
        if needed:
            pytest.fail(
                f"live test required for {', '.join(needed)} but variables not set: {', '.join(missing)}",
                pytrace=False,
            )
        pytest.skip(f"live test skipped — variables not set: {', '.join(missing)}")
    return values
