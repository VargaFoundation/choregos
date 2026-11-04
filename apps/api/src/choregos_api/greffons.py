# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external — former module of the extension API, removed in 0.23.0 (ADR 0098)
"""The former name of `choregos_api.extensions`: every name still resolves, with a warning."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .extensions import (
    _GESTURE_CHECKS,
    _HUMAN_TOOL_PROVIDERS,
    _INSTALLERS,
    _ROUTERS,
    _TOOL_PROVIDERS,
    GESTURES,
    SECTION_FEATURES,
    GestureRefused,
    GestureRequest,
    HumanToolProvider,
    ToolProvider,
    _uploads,
    check_gesture,
    declare_admin_section,
    declare_gesture_check,
    declare_human_tools,
    declare_router,
    declare_template_installer,
    declare_theme_provider,
    declare_tool_provider,
    declared_routers,
    declared_sections,
    human_tool_providers,
    reset,
    reset_gesture_checks,
    section_paths,
    signatures,
    template_installers,
    theme_provider,
    tool_providers,
)

__all__ = ["SECTION_FEATURES", "declare_theme_provider", "signatures", "theme_provider"]

declarer_un_routeur = declare_router  # lang: fr-ok alias:external
routeurs_declares = declared_routers  # lang: fr-ok alias:external
reinitialiser = reset  # lang: fr-ok alias:external
declarer_une_section_d_administration = declare_admin_section  # lang: fr-ok alias:external
sections_declarees = declared_sections  # lang: fr-ok alias:external
chemins_d_une_section = section_paths  # lang: fr-ok alias:external
FournisseurDOutils = ToolProvider  # lang: fr-ok alias:external
declarer_un_fournisseur_d_outils = declare_tool_provider  # lang: fr-ok alias:external
fournisseurs_d_outils = tool_providers  # lang: fr-ok alias:external
FournisseurHumain = HumanToolProvider  # lang: fr-ok alias:external
declarer_des_outils_pour_les_humains = declare_human_tools  # lang: fr-ok alias:external
fournisseurs_humains = human_tool_providers  # lang: fr-ok alias:external
declarer_un_installateur_de_gabarit = declare_template_installer  # lang: fr-ok alias:external
installateurs_de_gabarit = template_installers  # lang: fr-ok alias:external
GESTES = GESTURES  # lang: fr-ok alias:external
DemandeDeGeste = GestureRequest  # lang: fr-ok alias:external
GesteRefuse = GestureRefused  # lang: fr-ok alias:external
declarer_un_controle_de_geste = declare_gesture_check  # lang: fr-ok alias:external
controler = check_gesture  # lang: fr-ok alias:external
reinitialiser_les_controles = reset_gesture_checks  # lang: fr-ok alias:external
_ROUTEURS = _ROUTERS  # lang: fr-ok alias:external
_FOURNISSEURS_D_OUTILS = _TOOL_PROVIDERS  # lang: fr-ok alias:external
_FOURNISSEURS_HUMAINS = _HUMAN_TOOL_PROVIDERS  # lang: fr-ok alias:external
_INSTALLATEURS = _INSTALLERS  # lang: fr-ok alias:external
_CONTROLES = _GESTURE_CHECKS  # lang: fr-ok alias:external
_televersements = _uploads  # lang: fr-ok alias:external

replace_module(__name__, sys.modules["choregos_api.extensions"])
