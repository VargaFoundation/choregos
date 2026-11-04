# SPDX-License-Identifier: Apache-2.0
"""May a run start? The decision an extension adds without forking the orchestrator.

The core already bounds spending **per run** (the virtual key's ceiling) and concurrency **per
executor** (`maxActive`). It knows nothing of a limit that depends on something other than the run: a
suspended organisation, a monthly ceiling per organisation, a freeze window. These are operating
rules, not workflow rules, and they change from one deployment to another.

An extension declares them here (`declare_admission`); the orchestrator plays them in
`prepare_stage`, **before** it mints the gateway key and creates the run: a refusal therefore costs
nothing and leaves no key behind. It plays them **after** the replay path: a run already prepared is
never refused afterwards, which keeps the activity replayable (ADR 0008) and lets what is running
finish when a rule changes.

A check returns `None` to admit, or raises `AdmissionRefused(reason)`. The refusal stops the stage
without a retry, and the reason is what the work item shows: it must say what to do. A check that
raises ANYTHING ELSE is not a refusal, it is a failure; it goes up as it is and the activity is
retried, rather than passing for a decision.

The French names this module had before 0.21 (`declarer_une_admission`, `AdmissionRefusee`...) keep
working until 0.23.0 and warn (ADR 0098 §3 (f)).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .compat import EXTERNAL_ALIAS_REMOVAL, alias_names


@dataclass(frozen=True)
class RunRequest:
    """What is known of a run when deciding whether it starts."""

    org: str
    project: str
    work_item: str
    run_id: str
    role: str
    backend: str
    budget_usd: float


class AdmissionRefused(Exception):  # noqa: N818 - "refused" says what it is: a decision, not an error
    """The run does not start. `str()` is the reason shown on the work item."""


AdmissionCheck = Callable[[RunRequest], Awaitable[None] | None]

_CHECKS: dict[str, AdmissionCheck] = {}


def declare_admission(name: str, check: AdmissionCheck) -> None:
    """Called by an extension when it loads. The core declares none.

    Named, so that the reason of a refusal says WHO refused, and that a second load of the same
    extension replaces instead of doubling.
    """
    if not callable(check):
        raise TypeError(f"admission “{name}”: a callable is expected")
    _CHECKS[name] = check


def declared_admissions() -> tuple[str, ...]:
    return tuple(_CHECKS)


async def check(request: RunRequest) -> None:
    """Plays every declared check; raises `AdmissionRefused` at the first refusal, prefixed by its name."""
    for name, admission in _CHECKS.items():
        try:
            result = admission(request)
            if inspect.isawaitable(result):
                await result
        except AdmissionRefused as refusal:
            raise AdmissionRefused(f"{name} : {refusal}") from refusal


def reset() -> None:
    """For tests: no admission declared."""
    _CHECKS.clear()


DemandeDeRun = RunRequest  # lang: fr-ok alias:external
AdmissionRefusee = AdmissionRefused  # lang: fr-ok alias:external
Controle = AdmissionCheck  # lang: fr-ok alias:external
declarer_une_admission = declare_admission  # lang: fr-ok alias:external
admissions_declarees = declared_admissions  # lang: fr-ok alias:external
verifier = check  # lang: fr-ok alias:external
reinitialiser = reset  # lang: fr-ok alias:external
_CONTROLES = _CHECKS  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "DemandeDeRun": "RunRequest",  # lang: fr-ok alias:external
        "AdmissionRefusee": "AdmissionRefused",  # lang: fr-ok alias:external
        "Controle": "AdmissionCheck",  # lang: fr-ok alias:external
        "declarer_une_admission": "declare_admission",  # lang: fr-ok alias:external
        "admissions_declarees": "declared_admissions",  # lang: fr-ok alias:external
        "verifier": "check",  # lang: fr-ok alias:external
        "reinitialiser": "reset",  # lang: fr-ok alias:external
    },
    removed_in=EXTERNAL_ALIAS_REMOVAL,
)
