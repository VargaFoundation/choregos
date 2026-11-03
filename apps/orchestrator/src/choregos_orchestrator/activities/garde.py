# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module path, removed with its last importer (ADR 0098)
"""The former name of `activities.injection_guard`: the same module answers to both dotted names."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from . import injection_guard  # noqa: F401

replace_module(__name__, sys.modules["choregos_orchestrator.activities.injection_guard"])
