# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.templates`, ADR 0098
"""The former name of `choregos_api.services.templates`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .templates import (
    CATALOGUE_PREFIX,
    POLICY_WITHOUT_TEMPLATE,
    POLITIQUE_SANS_GABARIT,
    PREFIXE_CATALOGUE,
    WORKFLOW_SANS_GABARIT,
    WORKFLOW_WITHOUT_REPOSITORY,
    WORKFLOW_WITHOUT_TEMPLATE,
    Delivery,
    Livraison,
    _agent_document,
    _catalogue_agents,
    _folder_files,
    _policy_source,
    _skill_files,
    _template_path,
    _workflow_source,
    delivery,
    livraison,
    manifeste_du_gabarit,
    repertoire_des_gabarits,
    shipped_workflows,
    template_manifest,
    templates_directory,
)

__all__ = [
    "CATALOGUE_PREFIX",
    "POLICY_WITHOUT_TEMPLATE",
    "POLITIQUE_SANS_GABARIT",
    "PREFIXE_CATALOGUE",
    "WORKFLOW_SANS_GABARIT",
    "WORKFLOW_WITHOUT_REPOSITORY",
    "WORKFLOW_WITHOUT_TEMPLATE",
    "Delivery",
    "Livraison",
    "_agent_document",
    "_catalogue_agents",
    "_folder_files",
    "_policy_source",
    "_skill_files",
    "_template_path",
    "_workflow_source",
    "delivery",
    "livraison",
    "manifeste_du_gabarit",
    "repertoire_des_gabarits",
    "shipped_workflows",
    "template_manifest",
    "templates_directory",
]

replace_module(__name__, sys.modules["choregos_api.services.templates"])
