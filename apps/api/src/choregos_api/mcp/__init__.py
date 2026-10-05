# SPDX-License-Identifier: Apache-2.0
"""La porte MCP des clients externes : Claude Code, Claude Desktop, Cursor, VS Code… (ADR 0030).

Le side-car `choregos-tools` sert l'agent d'un run, de l'intérieur du pod. Cette porte sert
l'agent d'une PERSONNE, de l'extérieur : il agit avec les droits de cette personne, jamais plus,
et ne décide jamais — une décision exige une session ré-authentifiée, dans la console.
"""

from __future__ import annotations

from .transport import router

__all__ = ["router"]
