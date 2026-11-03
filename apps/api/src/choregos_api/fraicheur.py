# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module of the authentication freshness (ADR 0098)
"""The former name of `choregos_api.freshness`: tests patch `fraicheur.time`, the same `time` module."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .freshness import REAUTH, authentication_age, require_fresh_authentication

__all__ = ["REAUTH"]

age_de_l_authentification = authentication_age  # lang: fr-ok alias:internal
exiger_une_authentification_fraiche = require_fresh_authentication  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.freshness"])
