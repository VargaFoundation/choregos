# SPDX-License-Identifier: Apache-2.0
"""Le type de connecteur `mcp` (ADR 0034) : un vrai serveur MCP, joint par la plateforme."""

from .client import ClientMcp, ErreurMcp, OutilMcp, empreinte_du_schema

__all__ = ["ClientMcp", "ErreurMcp", "OutilMcp", "empreinte_du_schema"]
