# SPDX-License-Identifier: Apache-2.0
"""Une décision exige une authentification RÉCENTE (ADR 0030, ADR 0035).

Approuver une écriture dans l'annuaire, la commande d'un poste, la coupure d'un badge : la session
doit être fraîche, sinon un poste resté ouvert approuverait à la place de son propriétaire. Le 401
porte `step_up_required` et le chemin de ré-authentification : la console y renvoie, puis revient.
Le même format que la décision d'une action d'ontologie, que la console sait déjà suivre.
"""

from __future__ import annotations

import time
from typing import Any

from .errors import ApiError

REAUTH = "GET /api/v1/auth/login?reauth=1"


def age_de_l_authentification(principal: Any, maintenant: float | None = None) -> int | None:
    """En secondes ; `None` pour un jeton d'API ou une session sans date d'authentification."""
    authentifie_le = getattr(principal, "authentifie_le", None)
    if authentifie_le is None:
        return None
    return int(maintenant if maintenant is not None else time.time()) - int(authentifie_le)


def exiger_une_authentification_fraiche(principal: Any, minutes: int, maintenant: float | None = None) -> int:
    """Rend l'âge de l'authentification ; 401 `step_up_required` au-delà de `minutes`."""
    age = age_de_l_authentification(principal, maintenant)
    if age is None or age > minutes * 60:
        depuis = "an unknown time" if age is None else f"{age // 60} min"
        raise ApiError(
            401,
            "Authentification trop ancienne",
            f"this decision needs an authentication less than {minutes} min old; yours is {depuis} old",
            errors=[{"error": "step_up_required", "reauth": REAUTH}],
        )
    return age
