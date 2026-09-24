"""Ce à quoi un agent a touché, en une page.

POURQUOI
--------
Tout est déjà journalisé : chaque demande de permission, chaque outil appelé, chaque refus
et son motif. Mais c'est un journal ACP brut, ligne à ligne — sur un banc de deux agents,
225 événements en quelques minutes. Personne ne lit ça, donc personne ne sait à quoi
l'agent a touché. Un journal que personne ne lit n'est pas un audit.

Ce module **ne collecte rien de nouveau**. Il replie les événements déjà enregistrés en un
compte rendu : quels fichiers lus, lesquels écrits, quelles commandes exécutées, quelles
adresses jointes, quels outils du catalogue appelés — et ce qui a été REFUSÉ, avec le motif.

CE QU'IL NE FAIT PAS
--------------------
Il ne prouve pas qu'un agent n'a rien touché d'autre : la preuve d'isolation, c'est le
sandbox et la politique réseau, pas ce compte rendu. Il dit ce que l'agent a DEMANDÉ et ce
qu'on lui a répondu. Un agent qui écrit hors périmètre est refusé ici ET son diff est
vérifié après : deux mécanismes, pas un.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

#: Les natures d'accès qu'on sait nommer. Le reste tombe dans `autre` plutôt que d'être
#: perdu : un accès qu'on ne sait pas classer doit rester visible.
NATURES = ("read", "write", "execute", "network", "tool", "autre")


@dataclass(slots=True)
class Acces:
    """Une cible, et tout ce qui lui est arrivé."""

    nature: str
    cible: str
    demandes: int = 0
    refus: int = 0
    motifs: list[str] = field(default_factory=list)

    @property
    def refuse(self) -> bool:
        return self.refus > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "nature": self.nature,
            "cible": self.cible,
            "demandes": self.demandes,
            "refus": self.refus,
            "motifs": self.motifs,
        }


@dataclass(slots=True)
class RapportAcces:
    acces: list[Acces] = field(default_factory=list)
    evenements: int = 0

    @property
    def refuses(self) -> list[Acces]:
        return [a for a in self.acces if a.refuse]

    def par_nature(self, nature: str) -> list[Acces]:
        return [a for a in self.acces if a.nature == nature]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evenements": self.evenements,
            "refus": sum(a.refus for a in self.acces),
            "acces": [a.to_dict() for a in self.acces],
        }


def _cle(nature: str, cible: str) -> tuple[str, str]:
    return (nature if nature in NATURES else "autre", cible)


def rapport_d_acces(evenements: list[dict[str, Any]]) -> RapportAcces:
    """Replie le journal d'un run en un compte rendu d'accès.

    Les événements sont ceux qu'on enregistre déjà : `session/request_permission` porte la
    décision du garde-fou (nature, cible, autorisé, motif), et `choregos.tool.called` un
    appel au catalogue. Le reste du journal — la prose de l'agent — n'entre pas ici : ce
    compte rendu répond à « à quoi a-t-il touché », pas à « qu'a-t-il dit ».
    """
    groupes: OrderedDict[tuple[str, str], Acces] = OrderedDict()
    compte = 0
    for evenement in evenements:
        compte += 1
        type_ = str(evenement.get("type", ""))
        payload = evenement.get("payload") or {}
        if type_ == "session/request_permission":
            nature = str(payload.get("kind") or "autre")
            cible = str(payload.get("target") or "")
            if not cible:
                continue
            autorise = bool(payload.get("allowed", True))
            motif = str(payload.get("reason") or "")
        elif type_ in {"choregos.tool.called", "tool.called"}:
            nature, cible, autorise = "tool", str(payload.get("tool") or ""), True
            motif = str(payload.get("provider") or "")
            if not cible:
                continue
        else:
            continue

        acces = groupes.setdefault(_cle(nature, cible), Acces(nature=_cle(nature, cible)[0], cible=cible))
        acces.demandes += 1
        if not autorise:
            acces.refus += 1
            # Le motif du refus est l'information la plus utile de tout ce compte rendu :
            # « écriture interdite dans un fichier sensible » se lit, « denied » non.
            if motif and motif not in acces.motifs:
                acces.motifs.append(motif)

    # Les refus d'abord : c'est ce qu'on vient chercher. Puis par nombre de demandes.
    ordonne = sorted(groupes.values(), key=lambda a: (not a.refuse, -a.demandes, a.cible))
    return RapportAcces(acces=ordonne, evenements=compte)


__all__ = ["NATURES", "Acces", "RapportAcces", "rapport_d_acces"]
