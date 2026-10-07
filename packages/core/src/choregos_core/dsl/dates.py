# SPDX-License-Identifier: Apache-2.0
"""Une date tirée d'un champ du ticket (S20-05) : `fields.date_arrivee - 10d`.

Fonction pure, sans horloge : l'interpréteur la recalcule chaque fois qu'un champ change, et la
compare à SON heure (`workflow.now()`) — c'est ce qui la rend rejouable. Une date seule (`2026-11-02`)
vaut minuit UTC ; une date-heure garde son fuseau, et sans fuseau elle est en UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

_EXPRESSION = re.compile(r"^fields\.([a-z_][a-z0-9_]{0,62})(?:\s*([+-])\s*([0-9]{1,4})\s*([dhm]))?$")
_UNITES = {"d": "days", "h": "hours", "m": "minutes"}


class DateIllisible(ValueError):  # noqa: N818 - un refus nommé
    """Le champ existe mais ne dit pas une date."""


def champ_de(expression: str) -> str:
    """Le champ qu'une expression lit : `date_arrivee` pour `fields.date_arrivee - 10d`."""
    trouve = _EXPRESSION.fullmatch(expression.strip())
    if trouve is None:
        raise ValueError(f"expression de date illisible : {expression!r} (attendu : `fields.<champ> - 10d`)")
    return trouve.group(1)


def echeance(expression: str, champs: dict[str, Any]) -> datetime | None:
    """La date que dit l'expression pour ces champs, ou rien quand le champ n'est pas (encore)
    renseigné : on attend alors qu'il le soit, on ne part pas sans date."""
    trouve = _EXPRESSION.fullmatch(expression.strip())
    if trouve is None:
        raise ValueError(f"expression de date illisible : {expression!r} (attendu : `fields.<champ> - 10d`)")
    nom, signe, quantite, unite = trouve.groups()
    valeur = champs.get(nom)
    if valeur in (None, ""):
        return None
    base = _instant(nom, valeur)
    if signe is None:
        return base
    decalage = timedelta(**{_UNITES[unite]: int(quantite)})
    return base - decalage if signe == "-" else base + decalage


def _instant(nom: str, valeur: Any) -> datetime:
    texte = str(valeur).strip()
    try:
        if len(texte) == 10:
            jour = date.fromisoformat(texte)
            return datetime(jour.year, jour.month, jour.day, tzinfo=UTC)
        instant = datetime.fromisoformat(texte.replace("Z", "+00:00"))
    except ValueError as erreur:
        raise DateIllisible(f"the field `{nom}` is not a date: {texte!r}") from erreur
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)
