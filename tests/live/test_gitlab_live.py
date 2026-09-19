"""L'adaptateur GitLab contre gitlab.com (S13-04).

Ce que les tests à transport simulé ne peuvent pas prouver : que GitLab accepte nos
requêtes. Celui-ci crée une issue dans un projet bac à sable, la promène dans un cycle
Choregos complet, et la referme. Il ne touche qu'au projet qu'on lui donne.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.gitlab import GitLabTracker
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core.domain import NewItem, TrackerStateMapping

from .conftest import require

pytestmark = pytest.mark.live


@pytest.fixture
async def tracker() -> AsyncIterator[GitLabTracker]:
    """Un client par test : `httpx` lie ses connexions à la boucle qui les a ouvertes."""
    import os

    env = require("CHOREGOS_LIVE_GITLAB_TOKEN", "CHOREGOS_LIVE_GITLAB_PROJECT")
    client = RestClient(
        os.environ.get("CHOREGOS_LIVE_GITLAB_URL") or "https://gitlab.com",
        headers={"PRIVATE-TOKEN": env["CHOREGOS_LIVE_GITLAB_TOKEN"]},
        service="gitlab",
    )
    try:
        yield GitLabTracker(client, env["CHOREGOS_LIVE_GITLAB_PROJECT"])
    finally:
        await client.aclose()


async def test_cycle_complet_sur_un_vrai_ticket(tracker: GitLabTracker) -> None:
    """Création, lecture, label scopé, note de suivi réécrite, champs, candidats."""
    key = await tracker.create_item(
        NewItem(
            title="[choregos] validation de l'adaptateur",
            body="Ticket créé par `tests/live`. Fermé à la fin du test.",
            labels=["agent-ready"],
        )
    )
    assert key.endswith("#1") or "#" in key

    item = await tracker.fetch_item(key)
    assert item.title == "[choregos] validation de l'adaptateur"
    assert "agent-ready" in item.labels
    assert item.url and item.url.startswith("http")

    # L'état du DSL devient un label scopé — GitLab garantit l'exclusivité entre eux.
    await tracker.set_state(key, TrackerStateMapping(label="choregos::implement"))
    await tracker.set_state(
        key, TrackerStateMapping(label="choregos::verifying", remove_labels=["choregos::implement"])
    )
    apres = await tracker.fetch_item(key)
    assert "choregos::verifying" in apres.labels
    assert "choregos::implement" not in apres.labels, "les labels scopés sont exclusifs"

    # Un seul commentaire de suivi, réécrit (§4.3).
    def suivi(etape: str) -> str:
        return f"### Choregos\n\n| Étape | Statut |\n| --- | --- |\n| {etape} | ✅ |"

    await tracker.upsert_status_comment(key, suivi("implement"))
    await tracker.upsert_status_comment(key, suivi("verify"))
    relu = await tracker.fetch_item(key)
    suivis = [c for c in relu.comments if c.marker]
    assert len(suivis) == 1, f"{len(suivis)} commentaires de suivi au lieu d'un"
    assert "verify" in suivis[0].body

    # Les champs structurés vivent dans un bloc de métadonnées en fin de description.
    await tracker.set_fields(key, {"Coût (€)": "1,25", "Taille": "M", "Risque": "low"})
    avec_champs = await tracker.fetch_item(key)
    assert str(avec_champs.size) == "M"
    assert str(avec_champs.risk) == "low"
    assert "choregos:fields" not in avec_champs.body, "les métadonnées ne polluent pas le corps"

    # Le polling de secours retrouve le ticket par son étiquette.
    config = ProjectConfig(slug="sandbox", org="varga", repo=RepoConfig(url="https://gitlab.com/x/y.git"))
    assert key in await tracker.list_candidates(config)

    # Nettoyage : le ticket est fermé, pas laissé ouvert dans le projet.
    await tracker.client.request(
        "PUT",
        f"/api/v4/projects/{tracker.encoded}/issues/{tracker._iid(key)}",
        json={"state_event": "close"},
    )


async def test_un_projet_inexistant_donne_une_erreur_lisible(tracker: GitLabTracker) -> None:
    from choregos_adapters.errors import UpstreamError

    absent = GitLabTracker(tracker.client, "varga/ce-projet-n-existe-pas-999")
    with pytest.raises(UpstreamError) as error:
        await absent.fetch_item("varga/ce-projet-n-existe-pas-999#1")
    assert "gitlab" in str(error.value)
    assert error.value.status_code in {403, 404}


async def test_le_client_rest_suit_la_pagination(tracker: GitLabTracker) -> None:
    """La pagination réelle de GitLab (en-têtes `X-Next-Page`) ne casse pas notre boucle."""
    issues = await tracker.client.paginate(
        f"/api/v4/projects/{tracker.encoded}/issues", params={"state": "all"}
    )
    assert isinstance(issues, list)
