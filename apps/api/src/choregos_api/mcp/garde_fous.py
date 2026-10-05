# SPDX-License-Identifier: Apache-2.0
"""Ce qui borne la porte : débit par jeton, écritures par jour, taille des résultats."""

from __future__ import annotations

from datetime import timedelta

from choregos_core import utcnow
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditLog
from ..limiteur import Limiteur

#: Par jeton et par réplique : un client qui boucle s'arrête, un humain pressé ne le sent pas.
APPELS_PAR_MINUTE = 120
#: Par humain et par jour : une boucle d'agent qui créerait des tickets s'arrête vite.
ECRITURES_PAR_JOUR = 50
#: Sous les 25 000 jetons qu'accepte Claude Code par résultat d'outil.
TAILLE_MAX = 60_000

LIMITEUR = Limiteur(APPELS_PAR_MINUTE)


def tronquer(texte: str, taille: int = TAILLE_MAX) -> str:
    if len(texte) <= taille:
        return texte
    return texte[:taille] + f"\n\n[result_truncated: {len(texte) - taille} characters omitted]"


async def ecritures_du_jour(session: AsyncSession, user_id: str) -> int:
    """Les écritures faites par la porte depuis 24 h, lues dans l'audit : rien d'autre à tenir."""
    depuis = utcnow() - timedelta(days=1)
    lignes = (
        await session.execute(
            select(AuditLog.payload).where(
                AuditLog.action == "mcp.call", AuditLog.actor_id == user_id, AuditLog.ts >= depuis
            )
        )
    ).scalars()
    return sum(1 for payload in lignes if (payload or {}).get("ecriture"))
