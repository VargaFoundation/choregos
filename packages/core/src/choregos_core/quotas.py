# SPDX-License-Identifier: Apache-2.0
"""Le quota Kubernetes des namespaces d'un projet — fixé par un greffon selon l'organisation.

Chaque projet a ses namespaces (`proj-<slug>-runners`, `proj-<slug>-ci`) et un `ResourceQuota` qui
borne ce que ses runs consomment. Il était FIGÉ dans `gitops.py` (32 CPU, 96 Gi, 40 pods), le même
pour tous : en multi-organisation, le locataire qui paie peu obtenait autant que celui qui paie beaucoup,
et rien ne permettait de le dire.

Un greffon déclare `quota(org, projet)` ; le provisioning le joue au rendu des manifestes. Sans
greffon, ou si le greffon rend `None`, le défaut historique s'applique. Le quota s'écrit dans le dépôt
GitOps : il prend effet au PROCHAIN provisioning du projet, pas rétroactivement.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_CPU = re.compile(r"^\d+(\.\d+)?m?$")
_MEMOIRE = re.compile(r"^\d+(Ki|Mi|Gi|Ti)?$")


@dataclass(frozen=True)
class Quota:
    cpu: str = "32"
    memoire: str = "96Gi"
    pods: int = 40

    def __post_init__(self) -> None:
        # Un quota mal écrit ne se voit qu'au moment où Argo CD refuse de l'appliquer : on refuse ici.
        if not _CPU.match(self.cpu):
            raise ValueError(f"quota CPU illisible : {self.cpu!r} (ex. `8`, `500m`)")
        if not _MEMOIRE.match(self.memoire):
            raise ValueError(f"unreadable memory quota: {self.memoire!r} (e.g. `16Gi`)")
        if self.pods < 1:
            raise ValueError("a pods quota is at least 1")


DEFAUT = Quota()

Fournisseur = Callable[[str, str], Awaitable[Quota | None] | Quota | None]
_FOURNISSEURS: list[Fournisseur] = []


def declarer_un_quota(fournisseur: Fournisseur) -> None:
    """Appelée par un greffon à son chargement. Le premier qui rend un quota l'emporte."""
    if fournisseur not in _FOURNISSEURS:
        _FOURNISSEURS.append(fournisseur)


async def quota_pour(org: str, projet: str) -> Quota:
    for fournisseur in _FOURNISSEURS:
        resultat = fournisseur(org, projet)
        if inspect.isawaitable(resultat):
            resultat = await resultat
        if resultat is not None:
            return resultat
    return DEFAUT


def reinitialiser() -> None:
    _FOURNISSEURS.clear()
