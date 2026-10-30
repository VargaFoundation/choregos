# SPDX-License-Identifier: Apache-2.0
"""The identity connectors (ADR 0034): a directory where accounts are born, live and leave."""

from __future__ import annotations

from .entra import OPERATIONS_ENTRA, EntraIdentity, HorsDeLUnite, OutsideUnit

__all__ = ["OPERATIONS_ENTRA", "EntraIdentity", "HorsDeLUnite", "OutsideUnit"]
