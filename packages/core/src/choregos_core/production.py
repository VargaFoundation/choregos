# SPDX-License-Identifier: Apache-2.0
"""What production is, and the fakes it refuses (S80-03).

Production is `CHOREGOS_ENV` set to `staging` or `prod` (the chart's `global.environment`): the same
notion that already refuses a PostgreSQL superuser, the development sign-in and the demonstration
connectors there. Nothing else says "production": no second variable, no guess from a URL.

`CHOREGOS_FAKES` (ADR 0005) switches the whole platform to its in-memory fakes. That is what the
tests, the end-to-end suite and `make demo` run on, and it is a bench's switch only: on, it relaxes the
network egress control to `warn` (`choregos_core.egress.default_mode`) and hands the API an in-memory
secret store when no key is configured (`choregos_api.secret_store`). Every process that reads it, the
API, the outposts' door, the orchestrator's workers and the runner, calls `refuse_fakes_in_production`
before it serves anything, and stops there with the setting named.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: The `CHOREGOS_ENV` values that are production.
PRODUCTION_ENVIRONMENTS = frozenset({"staging", "prod"})
#: The variable that turns the fakes on, and the one that names the environment.
FAKES_VARIABLE = "CHOREGOS_FAKES"
ENVIRONMENT_VARIABLE = "CHOREGOS_ENV"
#: What pydantic reads as true for a boolean setting: the fakes are on for any of them.
_TRUE = frozenset({"1", "true", "yes", "on", "y", "t"})


class FakesInProductionError(RuntimeError):
    """A process was asked to start with the fakes on in a production environment."""


def is_production(environment: str | None) -> bool:
    return (environment or "").strip().lower() in PRODUCTION_ENVIRONMENTS


def fakes_on(value: str | bool | None) -> bool:
    """Whether a `CHOREGOS_FAKES` value turns the fakes on, read as the settings read it."""
    if isinstance(value, bool):
        return value
    return (value or "").strip().lower() in _TRUE


def refuse_fakes_in_production(*, environment: str | None, fakes: str | bool | None, process: str) -> None:
    """Stop `process` when the fakes are on in production, naming both settings and how to fix it."""
    if not (fakes_on(fakes) and is_production(environment)):
        return
    raise FakesInProductionError(
        f"{FAKES_VARIABLE} is on and {ENVIRONMENT_VARIABLE}={(environment or '').strip()}: the {process} "
        "refuses to start. The fakes (ADR 0005) relax the network egress control to `warn` and replace "
        "the secret store with an in-memory one; they are for dev, test and the demo only. Unset "
        f"{FAKES_VARIABLE} (the chart's `global.fakes: false`), or run this process outside staging and prod."
    )


def refuse_fakes_in_this_environment(process: str, environ: Mapping[str, str] | None = None) -> None:
    """`refuse_fakes_in_production`, read from a process environment (`os.environ` by default)."""
    values = os.environ if environ is None else environ
    refuse_fakes_in_production(
        environment=values.get(ENVIRONMENT_VARIABLE), fakes=values.get(FAKES_VARIABLE), process=process
    )
