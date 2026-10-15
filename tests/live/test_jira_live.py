"""The Jira adapter against a real Jira Cloud instance (S13-04).

What the fourteen simulated-transport tests cannot prove: that an instance accepts our
requests. This one creates a ticket in a sandbox project, takes it through a full Choregos cycle
— transition, rewritten follow-up comment, structured fields, fallback polling — and closes it
again.

It only touches the project it is given:

    CHOREGOS_LIVE_JIRA_URL=https://your-site.atlassian.net \\
    CHOREGOS_LIVE_JIRA_EMAIL=you@example.com \\
    CHOREGOS_LIVE_JIRA_TOKEN=<API token> \\
    CHOREGOS_LIVE_JIRA_PROJECT=SANDBOX \\
      uv run pytest tests/live/test_jira_live.py -m live

The token is an Atlassian *API token* (id.atlassian.com → Security → API tokens), not a
password: Jira Cloud no longer accepts password authentication.
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
    """One client per test: `httpx` binds its connections to the loop that opened them."""
    env = require(
        "CHOREGOS_LIVE_JIRA_URL",
        "CHOREGOS_LIVE_JIRA_EMAIL",
        "CHOREGOS_LIVE_JIRA_TOKEN",
        "CHOREGOS_LIVE_JIRA_PROJECT",
    )
    # Jira Cloud: Basic with `email:token`. Bearer is only valid for OAuth tokens, and answers
    # `AUTHENTICATED_FAILED` to an API token — a message that suggests a revoked token when it
    # is the scheme that is wrong.
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
    """Creation, read, transition, rewritten follow-up note, fields, candidates."""
    key = await tracker.create_item(
        NewItem(
            title="[choregos] adapter validation",
            body="Ticket created by `tests/live`. Closed at the end of the test.",
            labels=["agent-ready"],
        )
    )
    assert key.startswith(f"{tracker.project_key}-"), f"unexpected key: {key}"

    item = await tracker.fetch_item(key)
    assert item.title == "[choregos] adapter validation"
    assert "agent-ready" in item.labels
    assert item.url and item.url.startswith("http")

    # A transition, chosen by name among those the project's workflow offers.
    # This is the point the simulated transport cannot check: the transitions depend on the
    # workflow scheme of *your* project, not ours.
    await tracker.set_state(key, TrackerStateMapping(status="In Progress"))
    apres = await tracker.fetch_item(key)
    assert apres.state, "the ticket must carry a status after the transition"

    # A single follow-up comment, rewritten (§4.3).
    def suivi(etape: str) -> str:
        return f"### Choregos\n\n| Step | Status |\n| --- | --- |\n| {etape} | ✅ |"

    await tracker.upsert_status_comment(key, suivi("implement"))
    await tracker.upsert_status_comment(key, suivi("verify"))
    relu = await tracker.fetch_item(key)
    suivis = [c for c in relu.comments if c.marker]
    assert len(suivis) == 1, f"{len(suivis)} follow-up comments instead of one"
    assert "verify" in suivis[0].body

    # The structured fields: cleanly ignored if the project does not have them.
    await tracker.set_fields(
        key,
        {"Coût (€)": "1,25", "Taille": "M", "Risque": "low"},  # lang: fr-ok (legacy field names)
    )

    # The fallback polling finds the ticket by its label (JQL).
    config = ProjectConfig(
        slug="sandbox", org="varga", repo=RepoConfig(url="https://example.invalid/x/y.git")
    )
    assert key in await tracker.list_candidates(config)

    # Cleanup: the ticket is closed, not left open in the project.
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
    """The most useful check when nothing works: who are we, as far as Jira is concerned?

    `AUTHENTICATED_FAILED` on this call means an expired or revoked token, or a wrong
    authentication scheme — not "the site does not exist".
    """
    moi = await tracker.client.request("GET", "/rest/api/3/myself")
    assert moi.get("accountId"), f"unexpected response from /myself: {moi}"
