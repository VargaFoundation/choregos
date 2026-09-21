"""L'adaptateur Jira contre une vraie instance Jira Cloud (S13-04).

Ce que les quatorze tests à transport simulé ne peuvent pas prouver : qu'une instance
accepte nos requêtes. Celui-ci crée un ticket dans un projet bac à sable, le promène dans
un cycle Choregos complet — transition, commentaire de suivi réécrit, champs structurés,
polling de secours — et le referme.

Il ne touche qu'au projet qu'on lui donne :

    CHOREGOS_LIVE_JIRA_URL=https://votre-site.atlassian.net \\
    CHOREGOS_LIVE_JIRA_EMAIL=vous@exemple.fr \\
    CHOREGOS_LIVE_JIRA_TOKEN=<jeton d'API> \\
    CHOREGOS_LIVE_JIRA_PROJECT=SANDBOX \\
      uv run pytest tests/live/test_jira_live.py -m live

Le jeton est un *API token* Atlassian (id.atlassian.com → Security → API tokens), pas un
mot de passe : Jira Cloud n'accepte plus l'authentification par mot de passe.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import pytest
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.jira import JiraTracker
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core.domain import NewItem, TrackerStateMapping

from .conftest import require

pytestmark = pytest.mark.live


@pytest.fixture
async def tracker() -> AsyncIterator[JiraTracker]:
    """Un client par test : `httpx` lie ses connexions à la boucle qui les a ouvertes."""
    env = require(
        "CHOREGOS_LIVE_JIRA_URL",
        "CHOREGOS_LIVE_JIRA_EMAIL",
        "CHOREGOS_LIVE_JIRA_TOKEN",
        "CHOREGOS_LIVE_JIRA_PROJECT",
    )
    # Jira Cloud : Basic avec `email:token`. Le Bearer ne vaut que pour les jetons OAuth,
    # et répond `AUTHENTICATED_FAILED` à un API token — un message qui laisse croire à un
    # jeton révoqué alors que c'est le schéma qui est faux.
    credentials = f"{env['CHOREGOS_LIVE_JIRA_EMAIL']}:{env['CHOREGOS_LIVE_JIRA_TOKEN']}"
    token = base64.b64encode(credentials.encode()).decode()
    client = RestClient(
        env["CHOREGOS_LIVE_JIRA_URL"],
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
        service="jira",
    )
    try:
        yield JiraTracker(client, env["CHOREGOS_LIVE_JIRA_PROJECT"])
    finally:
        await client.aclose()


async def test_cycle_complet_sur_un_vrai_ticket(tracker: JiraTracker) -> None:
    """Création, lecture, transition, note de suivi réécrite, champs, candidats."""
    key = await tracker.create_item(
        NewItem(
            title="[choregos] validation de l'adaptateur",
            body="Ticket créé par `tests/live`. Fermé à la fin du test.",
            labels=["agent-ready"],
        )
    )
    assert key.startswith(f"{tracker.project_key}-"), f"clé inattendue : {key}"

    item = await tracker.fetch_item(key)
    assert item.title == "[choregos] validation de l'adaptateur"
    assert "agent-ready" in item.labels
    assert item.url and item.url.startswith("http")

    # Une transition, choisie par son nom parmi celles que le workflow du projet offre.
    # C'est le point que le transport simulé ne peut pas vérifier : les transitions
    # dépendent du schéma de workflow de *votre* projet, pas du nôtre.
    await tracker.set_state(key, TrackerStateMapping(status="In Progress"))
    apres = await tracker.fetch_item(key)
    assert apres.state, "le ticket doit porter un statut après la transition"

    # Un seul commentaire de suivi, réécrit (§4.3).
    def suivi(etape: str) -> str:
        return f"### Choregos\n\n| Étape | Statut |\n| --- | --- |\n| {etape} | ✅ |"

    await tracker.upsert_status_comment(key, suivi("implement"))
    await tracker.upsert_status_comment(key, suivi("verify"))
    relu = await tracker.fetch_item(key)
    suivis = [c for c in relu.comments if c.marker]
    assert len(suivis) == 1, f"{len(suivis)} commentaires de suivi au lieu d'un"
    assert "verify" in suivis[0].body

    # Les champs structurés : ignorés proprement si le projet ne les a pas.
    await tracker.set_fields(key, {"Coût (€)": "1,25", "Taille": "M", "Risque": "low"})

    # Le polling de secours retrouve le ticket par son étiquette (JQL).
    config = ProjectConfig(
        slug="sandbox", org="varga", repo=RepoConfig(url="https://example.invalid/x/y.git")
    )
    assert key in await tracker.list_candidates(config)

    # Nettoyage : le ticket est fermé, pas laissé ouvert dans le projet.
    transitions = await tracker.client.request("GET", f"/rest/api/3/issue/{key}/transitions")
    fermeture = next(
        (
            t
            for t in transitions.get("transitions", [])
            if t.get("to", {}).get("statusCategory", {}).get("key") == "done"
        ),
        None,
    )
    if fermeture is not None:
        await tracker.client.request(
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": fermeture["id"]}},
        )


async def test_un_projet_inexistant_donne_une_erreur_lisible(tracker: JiraTracker) -> None:
    from choregos_adapters.errors import UpstreamError

    absent = JiraTracker(tracker.client, "ZZZINEXISTANT")
    with pytest.raises(UpstreamError) as error:
        await absent.fetch_item("ZZZINEXISTANT-1")
    assert "jira" in str(error.value)
    assert error.value.status_code in {400, 403, 404}


async def test_le_jeton_est_accepte(tracker: JiraTracker) -> None:
    """Le contrôle le plus utile quand rien ne marche : qui sommes-nous pour Jira ?

    `AUTHENTICATED_FAILED` sur cet appel veut dire jeton expiré, révoqué, ou schéma
    d'authentification erroné — et non « le site n'existe pas ».
    """
    moi = await tracker.client.request("GET", "/rest/api/3/myself")
    assert moi.get("accountId"), f"réponse inattendue de /myself : {moi}"
