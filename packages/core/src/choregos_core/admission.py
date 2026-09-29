# SPDX-License-Identifier: Apache-2.0
"""Un run peut-il démarrer ? La décision qu'un greffon ajoute sans forker l'orchestrateur.

Le cœur borne déjà la dépense **par run** (plafond de la clé virtuelle) et la concurrence **par
exécuteur** (`maxActive`). Il ne sait rien d'une limite qui dépend d'autre chose que du run : une
organisation suspendue, un plafond mensuel par organisation, une fenêtre de gel. Ce sont des
règles d'exploitation, pas des règles du workflow, et elles changent d'un déploiement à l'autre.

Un greffon les déclare ici (`declarer_une_admission`) ; l'orchestrateur les joue dans
`prepare_stage`, **avant** d'émettre la clé de passerelle et de créer le run — un refus ne coûte
donc rien et ne laisse aucune clé derrière lui. Il les joue **après** le chemin de rejeu : un run
déjà préparé n'est jamais refusé après coup, ce qui garde l'activité rejouable (ADR 0008) et
laisse finir ce qui tourne quand une règle change.

Un contrôle rend `None` pour admettre, ou lève `AdmissionRefusee(raison)`. Le refus arrête
l'étape sans reprise, et la raison est ce que le ticket affiche : elle doit dire quoi faire.
Un contrôle qui lève AUTRE CHOSE n'est pas un refus, c'est une panne ; elle remonte telle quelle
et l'activité est retentée, plutôt que de passer pour une décision.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DemandeDeRun:
    """Ce qu'on sait d'un run au moment de décider s'il démarre."""

    org: str
    project: str
    work_item: str
    run_id: str
    role: str
    backend: str
    budget_usd: float


class AdmissionRefusee(Exception):  # noqa: N818 - « refusée » dit ce que c'est : une décision, pas une erreur
    """Le run ne démarre pas. `str()` est la raison montrée sur le ticket."""


Controle = Callable[[DemandeDeRun], Awaitable[None] | None]

_CONTROLES: dict[str, Controle] = {}


def declarer_une_admission(nom: str, controle: Controle) -> None:
    """Appelée par un greffon à son chargement. Le cœur n'en déclare aucune.

    Nommée, pour que la raison d'un refus dise QUI a refusé, et qu'un second chargement du même
    greffon remplace au lieu de doubler.
    """
    if not callable(controle):
        raise TypeError(f"admission « {nom} » : un appelable est attendu")
    _CONTROLES[nom] = controle


def admissions_declarees() -> tuple[str, ...]:
    return tuple(_CONTROLES)


async def verifier(demande: DemandeDeRun) -> None:
    """Joue chaque contrôle déclaré ; lève `AdmissionRefusee` au premier refus, préfixé de son nom."""
    for nom, controle in _CONTROLES.items():
        try:
            resultat = controle(demande)
            if inspect.isawaitable(resultat):
                await resultat
        except AdmissionRefusee as refus:
            raise AdmissionRefusee(f"{nom} : {refus}") from refus


def reinitialiser() -> None:
    """Pour les tests : aucune admission déclarée."""
    _CONTROLES.clear()
