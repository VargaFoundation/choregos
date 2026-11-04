# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module of the adapter wiring (ADR 0098)
"""The former name of `choregos_api.adapter_wiring`."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .adapter_wiring import attach_lexical_memory

brancher_memoire_lexicale = attach_lexical_memory  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.adapter_wiring"])
