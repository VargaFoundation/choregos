# SPDX-License-Identifier: Apache-2.0
"""Les connecteurs d'identité (ADR 0034) : un annuaire où naissent, vivent et partent des comptes."""

from .entra import OPERATIONS_ENTRA, EntraIdentity, HorsDeLUnite

__all__ = ["OPERATIONS_ENTRA", "EntraIdentity", "HorsDeLUnite"]
