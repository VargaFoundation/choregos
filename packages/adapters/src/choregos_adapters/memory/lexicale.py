# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_adapters.memory.lexical` (ADR 0098 §3 (a))."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .lexical import TOKEN_RE, Home, LexicalMemory, ModelesMemoire, Sessions, lexical_vector, similarity

__all__ = [
    "TOKEN_RE",
    "Home",
    "LexicalMemory",
    "ModelesMemoire",
    "Sessions",
    "lexical_vector",
    "similarity",
]

replace_module(__name__, sys.modules["choregos_adapters.memory.lexical"])
