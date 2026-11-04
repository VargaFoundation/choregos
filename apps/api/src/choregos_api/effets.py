# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external — former module of the effects API, removed in 0.23.0 (ADR 0098)
"""The former name of `choregos_api.effects`: every name still resolves, with a warning."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .effects import (
    EffectContext,
    EffectRefused,
    Execute,
    declare_effect,
    declared_effects,
    effect,
    effect_policy,
    render,
    reset,
)

EffetRefuse = EffectRefused  # lang: fr-ok alias:external
ContexteEffet = EffectContext  # lang: fr-ok alias:external
Executer = Execute  # lang: fr-ok alias:external
declarer_un_effet = declare_effect  # lang: fr-ok alias:external
politique_de_l_effet = effect_policy  # lang: fr-ok alias:external
effet = effect  # lang: fr-ok alias:external
effets_declares = declared_effects  # lang: fr-ok alias:external
rendre = render  # lang: fr-ok alias:external
reinitialiser = reset  # lang: fr-ok alias:external

replace_module(__name__, sys.modules["choregos_api.effects"])
