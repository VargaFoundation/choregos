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


MCP_DISTANT = """
outils:
  - name: rechercher_entreprise
    description: Cherche une entreprise chez un fournisseur tiers.
    provider: annuaire-externe
    groups: [rh, commerce]
    input_schema: { type: object, required: [siren], properties: { siren: { type: string } } }
    mcp:
      url: https://mcp.fournisseur.example/mcp
      tool: company_lookup
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: FOURNISSEUR_MCP_KEY
"""


def test_un_outil_a_une_source_et_une_seule() -> None:
    """Aucune et il ne fait rien ; les deux et on ne saurait laquelle employer. Le dire au
    chargement vaut mieux qu'au premier appel."""
    import pytest as _pytest

    with _pytest.raises(ValueError, match="doit déclarer"):
        charger_catalogue("outils:\n  - {name: vide, description: d, provider: p}")

    deux = MCP_DISTANT.replace(
        "    credential_env: FOURNISSEUR_MCP_KEY",
        "    credential_env: FOURNISSEUR_MCP_KEY\n    http: { url: https://exemple }",
    )
    with _pytest.raises(ValueError, match="doit déclarer"):
        charger_catalogue(deux)


def test_le_nom_distant_n_est_pas_celui_que_l_agent_voit() -> None:
    """L'indirection n'est pas cosmétique : elle permet de n'exposer qu'une PARTIE des
    outils d'un serveur, et de les nommer dans le vocabulaire de la maison."""
    outil = charger_catalogue(MCP_DISTANT).par_nom("rechercher_entreprise")
    assert outil is not None and outil.mcp is not None
    assert outil.mcp.tool == "company_lookup", "le nom chez le fournisseur"
    assert outil.name == "rechercher_entreprise", "le nom chez nous"
    assert outil.http is None


def test_un_outil_reserve_a_des_groupes_n_est_pas_ouvert_a_tous() -> None:
    """Le déploiement dit QUI a le droit ; le projet dit ce dont IL se sert. Sans ce
    verrou, la liste du projet serait le seul contrôle — et elle est modifiable par
    l'équipe du projet elle-même."""
    outil = charger_catalogue(MCP_DISTANT).par_nom("rechercher_entreprise")
    assert outil is not None
    assert outil.ouvert_a(["rh"])
    assert outil.ouvert_a(["commerce", "autre"])
    assert not outil.ouvert_a(["juridique"])
    assert not outil.ouvert_a([])


def test_sans_groupe_declare_l_outil_ne_restreint_rien() -> None:
    """Un catalogue sans groupes reste utilisable tel quel : la restriction s'ajoute."""
    outil = charger_catalogue(CATALOGUE).par_nom("recherche_profils")
    assert outil is not None and outil.groups == []
    assert outil.ouvert_a([]) and outil.ouvert_a(["n'importe quoi"])
