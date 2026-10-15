"""The GitLab adapter against gitlab.com (S13-04).

What the simulated-transport tests cannot prove: that GitLab accepts our requests. This one
creates an issue in a sandbox project, takes it through a full Choregos cycle, and closes it
again. It only touches the project it is given.
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
    """One client per test: `httpx` binds its connections to the loop that opened them."""
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
    """Creation, read, scoped label, rewritten follow-up note, fields, candidates."""
    key = await tracker.create_item(
        NewItem(
            title="[choregos] adapter validation",
            body="Ticket created by `tests/live`. Closed at the end of the test.",
            labels=["agent-ready"],
        )
    )
    assert key.endswith("#1") or "#" in key

    item = await tracker.fetch_item(key)
    assert item.title == "[choregos] adapter validation"
    assert "agent-ready" in item.labels
    assert item.url and item.url.startswith("http")

    # The DSL state becomes a scoped label — GitLab guarantees exclusivity between them.
    await tracker.set_state(key, TrackerStateMapping(label="choregos::implement"))
    await tracker.set_state(
        key, TrackerStateMapping(label="choregos::verifying", remove_labels=["choregos::implement"])
    )
    apres = await tracker.fetch_item(key)
    assert "choregos::verifying" in apres.labels
    assert "choregos::implement" not in apres.labels, "scoped labels are exclusive"

    # A single follow-up comment, rewritten (§4.3).
    def suivi(etape: str) -> str:
        return f"### Choregos\n\n| Step | Status |\n| --- | --- |\n| {etape} | ✅ |"

    await tracker.upsert_status_comment(key, suivi("implement"))
    await tracker.upsert_status_comment(key, suivi("verify"))
    relu = await tracker.fetch_item(key)
    suivis = [c for c in relu.comments if c.marker]
    assert len(suivis) == 1, f"{len(suivis)} follow-up comments instead of one"
    assert "verify" in suivis[0].body

    # Structured fields live in a metadata block at the end of the description.
    await tracker.set_fields(
        key,
        {"Coût (€)": "1,25", "Taille": "M", "Risque": "low"},  # lang: fr-ok (legacy field names)
    )
    avec_champs = await tracker.fetch_item(key)
    assert str(avec_champs.size) == "M"
    assert str(avec_champs.risk) == "low"
    assert "choregos:fields" not in avec_champs.body, "the metadata does not pollute the body"

    # The fallback polling finds the ticket by its label.
    config = ProjectConfig(slug="sandbox", org="varga", repo=RepoConfig(url="https://gitlab.com/x/y.git"))
    assert key in await tracker.list_candidates(config)

    # Cleanup: the ticket is closed, not left open in the project.
    await tracker.client.request(
        "PUT",
        f"/api/v4/projects/{tracker.encoded}/issues/{tracker._iid(key)}",
        json={"state_event": "close"},
    )


async def test_un_projet_inexistant_donne_une_erreur_lisible(tracker: GitLabTracker) -> None:
    from choregos_adapters.errors import UpstreamError

    absent = GitLabTracker(tracker.client, "varga/this-project-does-not-exist-999")
    with pytest.raises(UpstreamError) as error:
        await absent.fetch_item("varga/this-project-does-not-exist-999#1")
    assert "gitlab" in str(error.value)
    assert error.value.status_code in {403, 404}


async def test_le_client_rest_suit_la_pagination(tracker: GitLabTracker) -> None:
    """GitLab's real pagination (`X-Next-Page` headers) does not break our loop."""
    issues = await tracker.client.paginate(
        f"/api/v4/projects/{tracker.encoded}/issues", params={"state": "all"}
    )
    assert isinstance(issues, list)
