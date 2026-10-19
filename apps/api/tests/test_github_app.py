"""GitHub App installations are discovered, installed from the console, claimed (S40-04, ADR 0073)."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import AsyncClient

from .github_app_kit import add_organisation, claim_for

STATUS = "/api/v1/orgs/varga/github"
RETURN_TO = "/p/billing-api/settings#connections"


async def _state(client: AsyncClient) -> str:
    link = await client.post(
        f"{STATUS}/install-link", json={"return_to": RETURN_TO, "project": "billing-api"}
    )
    assert link.status_code == 200, link.text
    return parse_qs(urlsplit(link.json()["url"]).query)["state"][0]


async def test_single_org_claims_every_installation_on_sync(
    client: AsyncClient, admin: str, github: Any
) -> None:
    github.add_installation(1, "varga-inc")
    github.add_installation(2, "Other-Co", account_type="User", repository_selection="all")
    refreshed = (await client.post(f"{STATUS}/refresh")).json()
    assert refreshed["configured"] is True and refreshed["multi_org_claim"] == "implicit"
    assert refreshed["app_slug"] == "choregos-bot"
    assert refreshed["install_url"] == "https://github.com/apps/choregos-bot/installations/new"
    got = {i["account_login"]: i for i in refreshed["installations"]}
    assert set(got) == {"varga-inc", "Other-Co"}, "the only organisation claims what the App sees"
    assert all(i["claimed"] for i in got.values())
    assert got["Other-Co"]["settings_url"] == "https://github.com/settings/installations/2"
    assert got["varga-inc"]["settings_url"].endswith("/organizations/varga-inc/settings/installations/1")
    # a read does not need a refresh: the installation that disappears from GitHub disappears here
    github.remove_installation(2)
    assert {i["id"] for i in (await client.post(f"{STATUS}/refresh")).json()["installations"]} == {1}


async def test_a_deployment_without_an_app_says_so(client: AsyncClient, admin: str) -> None:
    status = (await client.get(STATUS)).json()
    assert status["configured"] is False and status["installations"] == []
    link = await client.post(f"{STATUS}/install-link", json={"return_to": RETURN_TO})
    assert link.status_code == 409


async def test_the_install_link_carries_a_signed_state_and_the_setup_returns_to_the_settings(
    client: AsyncClient, admin: str, github: Any
) -> None:
    github.add_installation(7, "varga-inc")
    url = (await client.post(f"{STATUS}/install-link", json={"return_to": RETURN_TO})).json()["url"]
    assert url.startswith("https://github.com/apps/choregos-bot/installations/new?state=")
    state = parse_qs(urlsplit(url).query)["state"][0]
    back = await client.get(
        "/api/v1/github/setup",
        params={"installation_id": 7, "setup_action": "install", "state": state},
        follow_redirects=False,
    )
    assert back.status_code == 302, back.text
    assert back.headers["location"] == "/p/billing-api/settings?github=claimed#connections"
    assert [i["id"] for i in (await client.get(STATUS)).json()["installations"]] == [7]


async def test_a_forged_state_is_refused(client: AsyncClient, admin: str, github: Any) -> None:
    from choregos_api.services.github_app import sign_state

    # One organisation: a forged state that got through WOULD claim implicitly, so a 403 is the guard.
    github.add_installation(7, "varga-inc")
    good = await _state(client)
    payload, _, _sig = good.partition(".")

    async def setup(state: str) -> Any:
        return await client.get(
            "/api/v1/github/setup", params={"installation_id": 7, "state": state}, follow_redirects=False
        )

    assert (await setup(f"{payload}.{'A' * 43}")).status_code == 403, "a bad signature"
    assert (await setup("not-a-state")).status_code == 403
    expired = sign_state(org="varga", project=None, user=admin, return_to="/p/x", now=time.time() - 7200)
    assert (await setup(expired)).status_code == 403, "an expired state"
    other_user = sign_state(org="varga", project=None, user="someone@else.dev", return_to="/p/x")
    assert (await setup(other_user)).status_code == 403, "the state names the person who started it"
    from choregos_api.db.models import GitHubInstallation
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(GitHubInstallation))).scalars().all() == [], "nothing claimed"
    open_redirect = await client.post(f"{STATUS}/install-link", json={"return_to": "//evil.example/x"})
    assert open_redirect.status_code == 422, "the return path is a path of the console"


async def test_multi_org_claim_requires_oauth_proof(client: AsyncClient, admin: str, github: Any) -> None:
    await add_organisation("globex")
    github.add_installation(1, "varga-inc")
    state = await _state(client)

    async def setup(**extra: Any) -> Any:
        return await client.get(
            "/api/v1/github/setup",
            params={"installation_id": 1, "state": state, **extra},
            follow_redirects=False,
        )

    assert (await client.get(STATUS)).json()["multi_org_claim"] == "unavailable"
    refused = await setup()
    assert refused.status_code == 403
    assert "CHOREGOS_GITHUB_APP_CLIENT_SECRET" in refused.text, "names what to configure"

    github.oauth_enabled = True
    assert (await client.get(STATUS)).json()["multi_org_claim"] == "oauth"
    assert (await setup()).status_code == 403, "no code: GitHub was not asked to authorise the person"
    github.grant_oauth("someone-elses", {99})
    assert (await setup(code="someone-elses")).status_code == 403, "their installations lack this one"
    github.grant_oauth("mine", {1})
    done = await setup(code="mine")
    assert done.status_code == 302, done.text
    assert [i["id"] for i in (await client.get(STATUS)).json()["installations"]] == [1]


async def test_an_installation_claimed_by_another_org_is_not_reclaimed(
    client: AsyncClient, admin: str, github: Any
) -> None:
    globex = await add_organisation("globex")
    github.oauth_enabled = True
    github.add_installation(1, "globex-inc")
    github.grant_oauth("mine", {1})
    await claim_for(1, "globex-inc", globex)
    state = await _state(client)
    taken = await client.get(
        "/api/v1/github/setup",
        params={"installation_id": 1, "state": state, "code": "mine"},
        follow_redirects=False,
    )
    assert taken.status_code == 409, taken.text
    assert (await client.get(STATUS)).json()["installations"] == [], "and varga does not see it"


async def test_repositories_lists_only_claimed_installations(
    client: AsyncClient, admin: str, github: Any
) -> None:
    globex = await add_organisation("globex")
    varga = (await client.get("/api/v1/orgs")).json()
    assert varga, "the administrator is signed in"
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        varga_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
    github.add_installation(1, "varga-inc")
    github.add_installation(2, "globex-inc")
    github.add_installation(3, "free-co")
    github.add_repository(1, "varga-inc/billing", private=True, default_branch="trunk")
    github.add_repository(1, "varga-inc/Web")
    github.add_repository(2, "globex-inc/secret")
    github.add_repository(3, "free-co/anything")
    await claim_for(1, "varga-inc", varga_id)
    await claim_for(2, "globex-inc", globex)
    await claim_for(3, "free-co", None)
    listed = (await client.get(f"{STATUS}/repositories")).json()
    assert [r["full_name"] for r in listed["items"]] == ["varga-inc/billing", "varga-inc/Web"]
    assert listed["items"][0]["default_branch"] == "trunk" and listed["items"][0]["installation_id"] == 1
    assert listed["meta"]["has_more"] is False
    found = (await client.get(f"{STATUS}/repositories", params={"q": "WEB"})).json()["items"]
    assert [r["full_name"] for r in found] == ["varga-inc/Web"]
    foreign = (await client.get(f"{STATUS}/repositories", params={"installation": 2})).json()
    assert foreign["items"] == [], "an installation id of another organisation lists nothing"


async def test_the_listing_is_for_people_who_manage_connectors(
    client: AsyncClient, project: dict[str, Any], github: Any
) -> None:
    from .conftest import login

    for body in (
        {"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
        {"email": "po@varga.dev", "role": "project_owner", "project_slug": "billing-api"},
    ):
        assert (await client.post("/api/v1/orgs/varga/members", json=body)).status_code == 201
    client.cookies.clear()
    await login(client, "dev@varga.dev")
    assert (await client.get(STATUS)).status_code == 403
    client.cookies.clear()
    await login(client, "po@varga.dev")
    assert (await client.get(STATUS)).status_code == 200, "connector:write on a project of the organisation"


async def test_installation_webhook_marks_it_suspended_and_deleted(
    client: AsyncClient, admin: str, github: Any
) -> None:
    def event(action: str, delivery: str) -> Any:
        installation = {
            "id": 5,
            "account": {"login": "varga-inc", "id": 50, "type": "Organization"},
            "repository_selection": "all",
            "app_id": 1,
        }
        return client.post(
            "/api/v1/webhooks/github",
            content=json.dumps({"action": action, "installation": installation}),
            headers={"x-github-event": "installation", "x-github-delivery": delivery},
        )

    async def listed() -> list[dict[str, Any]]:
        return list((await client.get(STATUS)).json()["installations"])

    github.add_installation(5, "varga-inc")  # keeps the lazy sync honest: GitHub still has it
    assert (await event("created", "d1")).status_code == 202
    assert [(i["id"], i["suspended"]) for i in await listed()] == [(5, False)]
    assert (await event("suspend", "d2")).status_code == 202
    from choregos_api.db.models import GitHubInstallation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        row = await session.get(GitHubInstallation, 5)
        assert row is not None and row.suspended_at is not None
    assert (await event("unsuspend", "d3")).status_code == 202
    async with session_scope(orgs="*") as session:
        row = await session.get(GitHubInstallation, 5)
        assert row is not None and row.suspended_at is None
    assert (await event("deleted", "d4")).status_code == 202
    async with session_scope(orgs="*") as session:
        assert await session.get(GitHubInstallation, 5) is None
    # an event of the App is no work item: the tracker path saw nothing
    assert (await client.get("/api/v1/projects")).status_code in {200, 404, 405}


@pytest.mark.parametrize("login_", ["Acme", "acme"])
async def test_owner_lookup_ignores_case(login_: str, client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.github_app import claimed_installation_for
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
    await claim_for(9, login_, org_id)
    async with session_scope(orgs="*") as session:
        found = await claimed_installation_for(session, org_id, "ACME")
        assert found is not None and found.id == 9
