# SPDX-License-Identifier: Apache-2.0
"""The instructions of a registry agent, rendered in a sandbox (ADR 0033).

A playbook is a deployment file, reviewed in a PR; an agent's instructions are written in the
console, by an organisation administrator. They are therefore rendered by an **isolated** Jinja
(`SandboxedEnvironment`): a template does not climb up to the Python classes (`{{ ''.__class__ }}`
is refused), calls no dangerous method, and reads only the variables it is given.

The same variables as a playbook: `ticket`, `spec`, `plan_markdown`, `inputs`, `allowed_paths`,
`context`, `project`. The frame the agent cannot modify (the output contract, the
invariants) is added AFTER the rendering, by the orchestrator.
"""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

#: Enough to render a template to check it, when a version is published.
SAMPLE_VARIABLES: dict[str, Any] = {
    "ticket": {
        "key": "DEMO-1",
        "title": "Onboarding of Camille",
        "body": "Arrives on 2 November.",
        "fields": {},
    },
    "spec": "",
    "plan_markdown": "",
    "inputs": {},
    "allowed_paths": [],
    "context": None,
    "project": None,
    #: The production indicator sources the run can read (S22-48); empty = none configured.
    "production_indicators": [],
}

_ENVIRONMENT = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def render_instructions(source: str, **variables: Any) -> str:
    """The template rendered in the sandbox; raises `TemplateError` (`SecurityError` included)."""
    return _ENVIRONMENT.from_string(source).render({**SAMPLE_VARIABLES, **variables})


def instruction_errors(source: str) -> list[str]:
    """What would keep the template from rendering, checked when a version is published."""
    try:
        render_instructions(source)
    except TemplateError as error:
        return [f"{type(error).__name__} : {error}"]
    return []


# Former names, still imported outside core until their own stories switch them (ADR 0098 §3 (b)).
VARIABLES_D_EXEMPLE = SAMPLE_VARIABLES  # lang: fr-ok alias:internal
rendre_les_instructions = render_instructions  # lang: fr-ok alias:internal
erreurs_des_instructions = instruction_errors  # lang: fr-ok alias:internal


__all__ = [
    "SAMPLE_VARIABLES",
    "VARIABLES_D_EXEMPLE",
    "erreurs_des_instructions",
    "instruction_errors",
    "render_instructions",
    "rendre_les_instructions",
]
