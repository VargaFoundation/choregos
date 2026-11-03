# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module of the bootstrap (ADR 0098)
"""The former name of `choregos_api.bootstrap`: `amorcer` is `bootstrap_installation`."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .bootstrap import bootstrap_installation

amorcer = bootstrap_installation  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.bootstrap"])
