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


# ──────── ce que l'agent envoie ne choisit pas la cible (2026-09-27) ────────

CHEMIN_GABARIT = """
outils:
  - name: lire_un_depot
    description: Lit les métadonnées publiques d'un dépôt GitHub.
    provider: github
    input_schema:
      type: object
      required: [owner, repo]
      properties:
        owner: { type: string, pattern: "^[A-Za-z0-9._-]+$" }
        repo: { type: string, pattern: "^[A-Za-z0-9._-]+$" }
    http:
      method: GET
      url: https://api.github.com/repos/{{ owner }}/{{ repo }}
    price_eur: 0.0
"""


def test_un_argument_ne_peut_pas_remonter_le_chemin_de_l_url() -> None:
    """Le catalogue existe pour que l'agent ne choisisse PAS l'URL (ADR 0014).

    L'hôte est écrit dans le catalogue, donc il ne bouge pas. Mais tant que la valeur était
    substituée nue, `owner` valant `..` remontait d'un segment : un outil censé lire
    `/repos/<org>/<dépôt>` atteignait n'importe quel autre point d'entrée du même hôte — **avec la
    clé du fournisseur que la plateforme y attache**. L'en-tête de ce fichier affirmait depuis
    toujours que « l'URL n'est jamais libre » ; elle l'était en partie.
    """
    outil = charger_catalogue(CHEMIN_GABARIT).par_nom("lire_un_depot")
    assert outil is not None

    # L'encodage seul ne suffisait PAS, et c'est le piège : `quote("..")` rend `..`, le point
    # étant un caractère non réservé. `/repos/../user` se normalise en `/user` — un autre point
    # d'entrée du même hôte. C'est la comparaison au préfixe qui refuse.
    with pytest.raises(ArgumentRefuseError) as refus:
        construire_requete(outil, {"owner": "..", "repo": "user"}, None)
    assert "sorti du chemin" in str(refus.value), refus.value

    # Une valeur qui tenterait d'ajouter un segment, une requête ou un fragment est encodée.
    requete = construire_requete(outil, {"owner": "org", "repo": "d?token=x#y"}, None)
    assert requete["url"] == "https://api.github.com/repos/org/d%3Ftoken%3Dx%23y"
    requete = construire_requete(outil, {"owner": "org/autre", "repo": "d"}, None)
    assert requete["url"] == "https://api.github.com/repos/org%2Fautre/d"

    # Et le cas nominal passe, inchangé.
    requete = construire_requete(outil, {"owner": "VargaFoundation", "repo": "choregos"}, None)
    assert requete["url"] == "https://api.github.com/repos/VargaFoundation/choregos"


def test_un_argument_hors_schema_est_refuse_avant_l_appel() -> None:
    """`input_schema` était DÉCORATIF : annoncé à l'agent, jamais vérifié.

    C'est l'`inputSchema` que `GET /internal/runs/{id}/tools` rend au format MCP. Un agent avait
    toutes les raisons de le croire contraignant. Un schéma annoncé et non appliqué décrit une
    garantie qui n'existe pas.
    """
    from choregos_core.catalogue import valider_les_arguments

    outil = charger_catalogue(CHEMIN_GABARIT).par_nom("lire_un_depot")
    assert outil is not None

    valider_les_arguments(outil, {"owner": "VargaFoundation", "repo": "choregos"})  # nominal

    with pytest.raises(ArgumentRefuseError) as refus:
        valider_les_arguments(outil, {"owner": "../..", "repo": "choregos"})
    assert "owner" in str(refus.value), f"le refus doit nommer l'argument : {refus.value}"

    with pytest.raises(ArgumentRefuseError):
        valider_les_arguments(outil, {"owner": "org"})  # `repo` requis et absent

    with pytest.raises(ArgumentRefuseError):
        valider_les_arguments(outil, {"owner": 42, "repo": "choregos"})  # mauvais type


def test_un_outil_sans_schema_n_impose_rien() -> None:
    """Le catalogue doit rester écrivable à la main pour un outil trivial."""
    from choregos_core.catalogue import valider_les_arguments

    outil = charger_catalogue(CATALOGUE).par_nom("recherche_profils")
    assert outil is not None
    outil.input_schema = {}
    valider_les_arguments(outil, {"n_importe": "quoi"})
