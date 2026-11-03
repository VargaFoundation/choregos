# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.agent_catalogue`, ADR 0098
"""The former name of `choregos_api.services.agent_catalogue`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .agent_catalogue import (
    DIVERGE,
    LEGACY,
    PREFIX,
    PREFIXE,
    EtatDansLOrganisation,
    OrgState,
    _installed_catalogue,
    _skill,
    _upgrade_skill,
    agents_nommes,
    auteur,
    author_of,
    catalogue,
    catalogue_instructions,
    catalogue_of,
    etat,
    from_catalogue,
    install,
    install_named_agents,
    installer,
    installer_les_agents_nommes,
    mettre_a_jour,
    named_agents,
    offer,
    offline_catalogue,
    offre,
    spec_de,
    spec_of,
    state,
    upgrade,
)

__all__ = [
    "DIVERGE",
    "LEGACY",
    "PREFIX",
    "PREFIXE",
    "EtatDansLOrganisation",
    "OrgState",
    "_installed_catalogue",
    "_skill",
    "_upgrade_skill",
    "agents_nommes",
    "auteur",
    "author_of",
    "catalogue",
    "catalogue_instructions",
    "catalogue_of",
    "etat",
    "from_catalogue",
    "install",
    "install_named_agents",
    "installer",
    "installer_les_agents_nommes",
    "mettre_a_jour",
    "named_agents",
    "offer",
    "offline_catalogue",
    "offre",
    "spec_de",
    "spec_of",
    "state",
    "upgrade",
]

replace_module(__name__, sys.modules["choregos_api.services.agent_catalogue"])
