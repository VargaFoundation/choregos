# SPDX-License-Identifier: Apache-2.0
"""The plugin's two entry points, declared by the package's `pyproject.toml`.

    [project.entry-points."choregos.plugins"]
    choregos-ontology = "choregos_ontology.service.plugin:register"
    [project.entry-points."choregos.migrations"]
    choregos-ontology = "choregos_ontology.service.plugin:MIGRATIONS"

**The plugin ships inactive.** It turns on only with `CHOREGOS_ONTOLOGY_TRIAL=1` (in the chart:
`global.extraEnv`, which gives it to the API, the workers and the migration Job; the former name of
the variable was removed in 0.24.0 and has no effect). Inactive, `register()`
declares nothing and `MIGRATIONS` names a directory without any revision: an installation that did not
ask for it serves none of its routes, announces none of its tools, and has none of its tables. This is
what lets the platform trial ship without changing anyone's platform.
"""

from __future__ import annotations

import os
from pathlib import Path

NAME = "choregos-ontology"
#: The name under which a template ships an ontology: `defaults.extensions.ontology`.
INSTALLER = "ontology"
#: The switch (ADR 0098 §3 (f)).
ACTIVATION = "CHOREGOS_ONTOLOGY_TRIAL"
_TRUE = {"1", "true", "yes"}
_HERE = Path(__file__).resolve().parent
VERSIONS = _HERE / "migrations" / "versions"
INACTIVE = _HERE / "migrations" / "inactive"


def active() -> bool:
    """The extension is active when `CHOREGOS_ONTOLOGY_TRIAL` is on."""
    return os.environ.get(ACTIVATION, "").strip().lower() in _TRUE


class _Location(os.PathLike[str]):
    """Where the revisions are, resolved when called and not when the module is loaded.

    `python -m choregos_api.migrate` reads the entry point's value, then turns it into a path: that is
    the moment the activation counts, not when the module was imported.
    """

    def __fspath__(self) -> str:
        return str(VERSIONS if active() else INACTIVE)

    def __repr__(self) -> str:
        return f"<migrations of the {NAME} plugin: {self.__fspath__()}>"


MIGRATIONS = _Location()


def register() -> None:
    """Called by the core's plugin loader when the API and the worker start; no effect when the trial
    is inactive."""
    if not active():
        return
    from choregos_api.effects import declare_effect
    from choregos_api.extensions import (
        declare_human_tools,
        declare_router,
        declare_template_installer,
        declare_tool_provider,
    )

    from choregos_ontology.service import store  # noqa: F401 - adds the tables to the core's Base
    from choregos_ontology.service.actions import (
        EFFECT,
        EVIDENCE,
        FORMER_EFFECT,
        FORMER_EVIDENCE,
        ontology_effect,
        ontology_evidence,
    )
    from choregos_ontology.service.api import install_from_template, router
    from choregos_ontology.service.tools import (
        call_tool,
        call_tool_for_a_person,
        list_tools,
        list_tools_for_a_person,
        pending_for_a_person,
    )

    declare_router(router)
    declare_tool_provider(NAME, list_tools, call_tool)
    # The effects the `ActionWorkflow` plays for the ontology (S20-08) — in the API that proposes, and
    # in the worker that executes. Their policy is the ontology's, decided at proposal time.
    declare_effect(EFFECT, ontology_effect, policy="approval")
    declare_effect(EVIDENCE, ontology_evidence, policy="approval")
    # The same two under their former kinds (ADR 0098 §4): a row written before S45-39 resolves.
    declare_effect(FORMER_EFFECT, ontology_effect, policy="approval")
    declare_effect(FORMER_EVIDENCE, ontology_evidence, policy="approval")
    # The gate of external MCP clients (ADR 0030): the same ontology, with the person's rights.
    declare_human_tools(NAME, list_tools_for_a_person, call_tool_for_a_person, pending_for_a_person)
    # A template that ships an ontology (`defaults.extensions.ontology`): it becomes the ontology of
    # the project being born (S20-09). Without the trial active, the project is born without it, and
    # the audit says so.
    declare_template_installer(INSTALLER, install_from_template)
