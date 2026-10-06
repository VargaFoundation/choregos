# SPDX-License-Identifier: Apache-2.0
"""Codes de sortie du runner : définis dans le cœur (`choregos_core.sorties`), que l'exécuteur lit
aussi pour dire pourquoi un pod est mort."""

from __future__ import annotations

from choregos_core.sorties import Exit

__all__ = ["Exit"]
