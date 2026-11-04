# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former module path, removed with its last importer (ADR 0098)
"""The former name of `activities.outcome`: the same module answers to both dotted names."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from . import outcome  # noqa: F401

replace_module(__name__, sys.modules["choregos_orchestrator.activities.outcome"])
