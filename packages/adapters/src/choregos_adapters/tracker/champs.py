# SPDX-License-Identifier: Apache-2.0
"""Les champs structurés que Choregos écrit dans un tracker, et leurs anciens noms (S22-21).

Jusqu'à S22-21, l'orchestrateur écrivait `Coût (€)`, `Taille` et `Risque`, et les gabarits créaient
le board GitHub Projects sous ces noms. Tout ce qu'un utilisateur lit est en anglais (ADR 0039) : les
champs s'appellent désormais `Cost (€)`, `Size` et `Risk`. Un board, un projet Jira ou un bloc de
métadonnées GitLab créé avant garde ses noms français : le provisioning renomme le champ du board,
et, d'ici là, chaque adaptateur écrit dans celui des deux qui existe plutôt que de perdre la valeur.

Les seuls littéraux français de ce module sont ces anciens noms : des noms qu'un tracker porte
encore, pas du texte que Choregos produit.
"""

from __future__ import annotations

from collections.abc import Container

COUT = "Cost (€)"
TAILLE = "Size"
RISQUE = "Risk"
RUN = "Run"

#: Nom d'aujourd'hui → nom d'avant S22-21. À retirer une version après S22-21.
ANCIENS_NOMS: dict[str, str] = {COUT: "Coût (€)", TAILLE: "Taille", RISQUE: "Risque"}

#: Nom d'avant → nom d'aujourd'hui.
NOMS_ACTUELS: dict[str, str] = {ancien: actuel for actuel, ancien in ANCIENS_NOMS.items()}


def nom_actuel(nom: str) -> str:
    """Le nom d'aujourd'hui d'un champ, qu'on le donne sous l'ancien ou le nouveau."""
    return NOMS_ACTUELS.get(nom, nom)


def nom_present(nom: str, presents: Container[str]) -> str | None:
    """Le nom sous lequel le champ existe : le nouveau d'abord, l'ancien sinon, `None` s'il n'y est pas."""
    actuel = nom_actuel(nom)
    if actuel in presents:
        return actuel
    ancien = ANCIENS_NOMS.get(actuel)
    if ancien is not None and ancien in presents:
        return ancien
    return None
