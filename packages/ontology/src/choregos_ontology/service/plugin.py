# SPDX-License-Identifier: Apache-2.0
"""The plugin's two entry points, declared by the package's `pyproject.toml`.

    [project.entry-points."choregos.plugins"]
    choregos-ontology = "choregos_ontology.service.plugin:register"
    [project.entry-points."choregos.migrations"]
    choregos-ontology = "choregos_ontology.service.plugin:MIGRATIONS"

**The plugin ships inactive.** It turns on only with `CHOREGOS_ESSAI_ONTOLOGIE=1` (in the chart:
`global.extraEnv`, which gives it to the API, the workers and the migration Job). Inactive, `register()`
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
#: The switch, as deployments already set it: a configuration name, kept as it is.
ACTIVATION = "CHOREGOS_ESSAI_ONTOLOGIE"  # lang: fr-ok - an environment variable deployments set
_HERE = Path(__file__).resolve().parent
VERSIONS = _HERE / "migrations" / "versions"
INACTIVE = _HERE / "migrations" / "inactive"


def active() -> bool:
    return os.environ.get(ACTIVATION, "").strip().lower() in {"1", "true", "yes"}


class _Location(os.PathLike[str]):
    """Where the revisions are, resolved when called and not when the module is loaded.

    `python -m choregos_api.migrer` reads the entry point's value, then turns it into a path: that is
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
    from choregos_api.effets import declarer_un_effet
    from choregos_api.greffons import (
        declarer_des_outils_pour_les_humains,
        declarer_un_fournisseur_d_outils,
        declarer_un_installateur_de_gabarit,
        declarer_un_routeur,
    )

    from choregos_ontology.service import store  # noqa: F401 - adds the tables to the core's Base
    from choregos_ontology.service.actions import EFFECT, EVIDENCE, ontology_effect, ontology_evidence
    from choregos_ontology.service.api import install_from_template, router
    from choregos_ontology.service.tools import (
        call_tool,
        call_tool_for_a_person,
        list_tools,
        list_tools_for_a_person,
        pending_for_a_person,
    )

    declarer_un_routeur(router)
    declarer_un_fournisseur_d_outils(NAME, list_tools, call_tool)
    # The effects the `ActionWorkflow` plays for the ontology (S20-08) — in the API that proposes, and
    # in the worker that executes. Their policy is the ontology's, decided at proposal time.
    declarer_un_effet(EFFECT, ontology_effect, politique="approval")
    declarer_un_effet(EVIDENCE, ontology_evidence, politique="approval")
    # The gate of external MCP clients (ADR 0030): the same ontology, with the person's rights.
    declarer_des_outils_pour_les_humains(
        NAME, list_tools_for_a_person, call_tool_for_a_person, pending_for_a_person
    )
    # A template that ships an ontology (`defaults.extensions.ontology`): it becomes the ontology of
    # the project being born (S20-09). Without the trial active, the project is born without it, and
    # the audit says so.
    declarer_un_installateur_de_gabarit(INSTALLER, install_from_template)
