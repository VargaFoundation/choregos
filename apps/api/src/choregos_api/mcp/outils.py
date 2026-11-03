# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former MCP door module (ADR 0098), removed by S45-48
"""The former name of `choregos_api.mcp.tools`: the same module object under its English name."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .tools import INSTRUCTIONS, Context, Executor, Tool, ToolRefused, announced_tools, core_tools

__all__ = ["INSTRUCTIONS"]

OutilRefuse = ToolRefused  # lang: fr-ok alias:internal
Contexte = Context  # lang: fr-ok alias:internal
Executeur = Executor  # lang: fr-ok alias:internal
Outil = Tool  # lang: fr-ok alias:internal
annonces = announced_tools  # lang: fr-ok alias:internal
outils = core_tools  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.mcp.tools"])
