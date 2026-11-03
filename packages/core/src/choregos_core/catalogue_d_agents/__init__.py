# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external — former package of the agent catalogue, removed in 0.22.0 (ADR 0098)
"""The former name of `choregos_core.agent_catalogue`: the same module, under its old path.

`import choregos_core.catalogue_d_agents` gives `choregos_core.agent_catalogue` itself, so a patch
on one is a patch on the other, and `python -m choregos_core.catalogue_d_agents.evals` runs the
evals of the new package (its `__path__` is the new folder).
"""

from __future__ import annotations

import sys

from choregos_core.agent_catalogue import (
    FOLDER,
    Catalogue,
    CatalogueEntry,
    CatalogueSkill,
    EvalCase,
    _context,
    catalogue,
    catalogue_skills,
    entries,
    entry,
    entry_of,
    load,
    of_plugin,
    offline_directory,
    skill_files,
)
from choregos_core.compat import replace_module

__all__ = [
    "Catalogue",
    "CatalogueSkill",
    "EvalCase",
    "catalogue",
    "entry_of",
    "load",
    "of_plugin",
    "offline_directory",
]

EntreeDuCatalogue = CatalogueEntry  # lang: fr-ok alias:external
DOSSIER = FOLDER  # lang: fr-ok alias:external
entrees = entries  # lang: fr-ok alias:external
entree = entry  # lang: fr-ok alias:external
skills_du_catalogue = catalogue_skills  # lang: fr-ok alias:external
fichiers_de_la_skill = skill_files  # lang: fr-ok alias:external
_contexte = _context  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_core.agent_catalogue"])
