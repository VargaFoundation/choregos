"""Catalogue d'outils : la clé reste côté plateforme, et l'URL n'est jamais libre."""

from __future__ import annotations

import pytest
from choregos_core.catalogue import ArgumentRefuseError, charger_catalogue, construire_requete

CATALOGUE = """
outils:
  - name: recherche_profils
    description: Cherche des profils correspondant à des critères.
    provider: annuaire
    categories: [rh]
    input_schema:
      type: object
      required: [metier]
      properties:
        metier: { type: string }
        ville: { type: string }
    http:
      method: GET
      url: https://api.annuaire.example/v1/search
      query: { q: "{{ metier }}", ville: "{{ ville }}" }
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: ANNUAIRE_API_KEY
    price_eur: 0.02
"""


def test_la_cle_n_apparait_que_dans_la_requete_sortante() -> None:
    """Le catalogue est lu dans une PR : il porte le NOM de la variable, jamais la clé.
    Et la clé rendue ici ne repart ni vers l'agent, ni dans le journal."""
    outil = charger_catalogue(CATALOGUE).par_nom("recherche_profils")
    assert outil is not None
    assert outil.credential_env == "ANNUAIRE_API_KEY"
    assert "secret" not in CATALOGUE.lower()

    requete = construire_requete(outil, {"metier": "data engineer", "ville": "Lille"}, "clé-du-coffre")
    assert requete["headers"]["Authorization"] == "Bearer clé-du-coffre"
    assert requete["params"] == {"q": "data engineer", "ville": "Lille"}
    assert requete["url"] == "https://api.annuaire.example/v1/search"


def test_un_argument_manquant_est_refuse_plutot_que_vide() -> None:
    """Remplir un gabarit avec une chaîne vide enverrait une requête qui a l'air valide
    et qui ne cherche rien — l'agent croirait à un résultat vide."""
    outil = charger_catalogue(CATALOGUE).par_nom("recherche_profils")
    assert outil is not None
    with pytest.raises(ArgumentRefuseError, match="ville"):
        construire_requete(outil, {"metier": "data engineer"}, "clé")


def test_l_agent_ne_choisit_ni_l_url_ni_la_methode() -> None:
    """Un outil dont l'agent fixerait l'URL ferait de la plateforme un proxy ouvert,
    appelant n'importe quoi avec ses propres clés."""
    outil = charger_catalogue(CATALOGUE).par_nom("recherche_profils")
    assert outil is not None
    requete = construire_requete(
        outil, {"metier": "x", "ville": "y", "url": "http://169.254.169.254/"}, "clé"
    )
    assert requete["url"].startswith("https://api.annuaire.example/")
    assert requete["method"] == "GET"
