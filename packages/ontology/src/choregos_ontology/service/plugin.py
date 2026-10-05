# SPDX-License-Identifier: Apache-2.0
"""Les deux points d'entrée du greffon, déclarés par le `pyproject.toml` du paquet.

    [project.entry-points."choregos.plugins"]
    choregos-ontology = "choregos_ontology.service.plugin:brancher"
    [project.entry-points."choregos.migrations"]
    choregos-ontology = "choregos_ontology.service.plugin:MIGRATIONS"

**Le greffon est livré inactif.** Il ne s'active que si `CHOREGOS_ESSAI_ONTOLOGIE=1` (dans le chart :
`global.extraEnv`, qui le donne à l'API, aux workers et au Job de migration). Inactif, `brancher()`
ne déclare rien et `MIGRATIONS` désigne un répertoire sans révision : une installation qui ne l'a pas
demandé ne sert aucune de ses routes, n'annonce aucun de ses outils, et n'a aucune de ses tables.
C'est ce qui permet de livrer l'essai du socle sans changer la plateforme de quiconque.
"""

from __future__ import annotations

import os
from pathlib import Path

NOM = "choregos-ontology"
#: Le nom sous lequel un gabarit livre une ontologie : `defaults.extensions.ontology`.
INSTALLATEUR = "ontology"
ACTIVATION = "CHOREGOS_ESSAI_ONTOLOGIE"
_ICI = Path(__file__).resolve().parent
VERSIONS = _ICI / "migrations" / "versions"
INACTIF = _ICI / "migrations" / "inactif"


def actif() -> bool:
    return os.environ.get(ACTIVATION, "").strip().lower() in {"1", "true", "yes"}


class _Emplacement(os.PathLike[str]):
    """L'emplacement des révisions, résolu à l'appel et non au chargement du module.

    `python -m choregos_api.migrer` lit la valeur du point d'entrée, puis la convertit en chemin :
    c'est à cet instant que l'activation compte, pas quand le module a été importé.
    """

    def __fspath__(self) -> str:
        return str(VERSIONS if actif() else INACTIF)

    def __repr__(self) -> str:
        return f"<migrations du greffon {NOM} : {self.__fspath__()}>"


MIGRATIONS = _Emplacement()


def brancher() -> None:
    """Appelée par `charger_les_greffons()` au démarrage de l'API et du worker ; sans effet si l'essai
    est inactif."""
    if not actif():
        return
    from choregos_api.effets import declarer_un_effet
    from choregos_api.greffons import (
        declarer_des_outils_pour_les_humains,
        declarer_un_fournisseur_d_outils,
        declarer_un_installateur_de_gabarit,
        declarer_un_routeur,
    )

    from choregos_ontology.service import store  # noqa: F401 - inscrit les tables dans le Base du cœur
    from choregos_ontology.service.actions import EFFET, PREUVE, effet_de_l_ontologie, preuve_de_l_ontologie
    from choregos_ontology.service.api import installer_depuis_un_gabarit, router
    from choregos_ontology.service.outils import (
        appeler,
        appeler_pour_un_humain,
        en_attente_pour_un_humain,
        lister,
        lister_pour_un_humain,
    )

    declarer_un_routeur(router)
    declarer_un_fournisseur_d_outils(NOM, lister, appeler)
    # Les effets que l'`ActionWorkflow` joue pour l'ontologie (S20-08) — dans l'API qui propose, et
    # dans le worker qui exécute. Leur politique est celle de l'ontologie, décidée à la proposition.
    declarer_un_effet(EFFET, effet_de_l_ontologie, politique="approval")
    declarer_un_effet(PREUVE, preuve_de_l_ontologie, politique="approval")
    # La porte MCP des clients externes (ADR 0030) : la même ontologie, avec les droits de l'humain.
    declarer_des_outils_pour_les_humains(
        NOM, lister_pour_un_humain, appeler_pour_un_humain, en_attente_pour_un_humain
    )
    # Un gabarit qui livre une ontologie (`defaults.extensions.ontology`) : elle devient celle du
    # projet qui naît (S20-09). Sans l'essai actif, le projet naît sans, et l'audit le dit.
    declarer_un_installateur_de_gabarit(INSTALLATEUR, installer_depuis_un_gabarit)
