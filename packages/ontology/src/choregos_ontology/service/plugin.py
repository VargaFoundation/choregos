# SPDX-License-Identifier: Apache-2.0
"""Les deux points d'entrée du greffon, tels que `pip install` les déclare au cœur.

    [project.entry-points."choregos.plugins"]
    choregos-ontology = "choregos_ontology.service.plugin:brancher"
    [project.entry-points."choregos.migrations"]
    choregos-ontology = "choregos_ontology.service.plugin:MIGRATIONS"

Ils ne sont PAS déclarés dans le `pyproject.toml` du paquet de l'espace de travail : installés là,
ils brancheraient le greffon dans chaque application que construit la suite du cœur, dont
`test_paths_match_contract`, qui exige que l'API serve exactement le contrat du cœur. Les tests du
greffon l'installent comme le ferait `pip`, avec un `.dist-info` trouvé par `importlib.metadata`.
"""

from __future__ import annotations

from pathlib import Path

NOM = "choregos-ontology"
MIGRATIONS = Path(__file__).resolve().parent / "migrations" / "versions"


def brancher() -> None:
    """Appelée par `charger_les_greffons()` au démarrage de l'API."""
    from choregos_api.greffons import declarer_un_fournisseur_d_outils, declarer_un_routeur

    from choregos_ontology.service import store  # noqa: F401 - inscrit les tables dans le Base du cœur
    from choregos_ontology.service.api import router
    from choregos_ontology.service.outils import appeler, lister

    declarer_un_routeur(router)
    declarer_un_fournisseur_d_outils(NOM, lister, appeler)
