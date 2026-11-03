# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module of the rate limiter (ADR 0098)
"""The former name of `choregos_api.rate_limit`: `Limiteur` is `RateLimiter`."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .rate_limit import MAX_KEY_CHARS, MAX_KEYS, PREFIXES, WINDOW_S, RateLimiter, client_address, install

__all__ = ["MAX_KEYS", "MAX_KEY_CHARS", "PREFIXES", "client_address"]

Limiteur = RateLimiter  # lang: fr-ok alias:internal
installer = install  # lang: fr-ok alias:internal
FENETRE_S = WINDOW_S  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.rate_limit"])
