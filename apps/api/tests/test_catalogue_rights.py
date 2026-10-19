"""Catalogue rights (ADR 0074, S40-09a): who sees, installs and enables an MCP server or a connector.

`decide()` answers every question the routers ask. The expected answers below are written out as the
list of principals each cell lets through; nothing is derived from the implementation, and every
other principal must be refused with a reason.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_api.rbac import Principal
from choregos_api.services.catalogue_rights import CatalogueAction, decide, projects_where
from choregos_contracts import Role
from httpx import AsyncClient

from .agent_scopes_kit import add_member, as_user, make_project
from .catalogue_kit import project_only

ORG = "varga"
BASE = "/api/v1/orgs/varga"


def _who(user: str, org: Role | None = None, **projects: Role) -> Principal:
    return Principal(
        user_id=user,
        email=f"{user}@varga.dev",
        org_roles={ORG: org} if org else {},
        project_roles={f"{ORG}/{slug}": role for slug, role in projects.items()},
    )


PRINCIPALS: dict[str, Principal] = {
    "admin": _who("u-admin", Role.ORG_ADMIN),
    "owner_dev": _who("u-owner-dev", dev=Role.PROJECT_OWNER),
    "owner_ops": _who("u-owner-ops", ops=Role.PROJECT_OWNER),
    "developer": _who("u-dev", Role.DEVELOPER),
    "project_developer": _who("u-pdev", dev=Role.DEVELOPER),
    "viewer": _who("u-viewer", Role.VIEWER),
    "outsider": _who("u-outsider"),
}
MEMBERS = set(PRINCIPALS) - {"outsider"}

# (action, project) -> the principals let through. The matrix of the module docstring of
# `services/catalogue_rights.py`, written out again on purpose.
MATRIX: dict[tuple[CatalogueAction, str | None], set[str]] = {
    (CatalogueAction.SEE, None): MEMBERS,
    (CatalogueAction.INSTALL, None): {"admin"},
    (CatalogueAction.SET_AVAILABILITY, None): {"admin"},
    (CatalogueAction.CONFIGURE, "dev"): {"admin", "owner_dev"},
    (CatalogueAction.CONFIGURE, "ops"): {"admin", "owner_ops"},
    (CatalogueAction.ENABLE, "dev"): {"admin", "owner_dev"},
    (CatalogueAction.ENABLE, "ops"): {"admin", "owner_ops"},
}


@pytest.mark.parametrize(("action", "project"), list(MATRIX))
def test_decide_matches_the_matrix_cell_by_cell(action: CatalogueAction, project: str | None) -> None:
    expected = MATRIX[(action, project)]
    for name, principal in PRINCIPALS.items():
        decision = decide(principal, ORG, action, project_slug=project)
        assert decision.allowed is (name in expected), f"{name} / {action} / {project}"
        assert (decision.reason is None) is decision.allowed, "a refusal says why; a yes says nothing"


def test_a_project_action_without_a_project_is_refused() -> None:
    assert not decide(PRINCIPALS["admin"], ORG, CatalogueAction.ENABLE).allowed


def test_decide_does_not_cross_organisations() -> None:
    admin_elsewhere = Principal(user_id="x", email="x@y.dev", org_roles={"globex": Role.ORG_ADMIN})
    for action in CatalogueAction:
        assert not decide(admin_elsewhere, ORG, action, project_slug="dev").allowed


def test_projects_where_keeps_the_projects_the_caller_may_act_in() -> None:
    visible = ["dev", "ops"]
    assert projects_where(PRINCIPALS["admin"], ORG, CatalogueAction.ENABLE, visible) == ["dev", "ops"]
    assert projects_where(PRINCIPALS["owner_dev"], ORG, CatalogueAction.ENABLE, visible) == ["dev"]
    assert projects_where(PRINCIPALS["project_developer"], ORG, CatalogueAction.ENABLE, visible) == []


# ───────────────────────────── through the API ─────────────────────────────


async def _world(client: AsyncClient, org: str) -> None:
    await make_project(client, "billing")
    await make_project(client, "checkout")
    await add_member(client, "owner@varga.dev", "project_owner", "billing")
    await add_member(client, "dev@varga.dev", "developer", "billing")
    await project_only(client, "owner@varga.dev")
    await project_only(client, "dev@varga.dev")


def _by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in body["entries"]}


async def test_a_project_only_member_reads_both_catalogues(client: AsyncClient, org: str) -> None:
    """A developer on one project, no organisation role: 403 before ADR 0074."""
    await _world(client, org)
    await project_only(client, "dev@varga.dev")
    mcp = await client.get(f"{BASE}/mcp-catalogue")
    assert mcp.status_code == 200, mcp.text
    connectors = await client.get(f"{BASE}/connector-catalogue")
    assert connectors.status_code == 200, connectors.text


async def test_a_stranger_reads_neither(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await project_only(client, "stranger@elsewhere.dev")
    assert (await client.get(f"{BASE}/mcp-catalogue")).status_code == 403
    assert (await client.get(f"{BASE}/connector-catalogue")).status_code == 403


async def test_install_is_the_administrators_and_the_entry_says_so(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    admin_view = _by_id((await client.get(f"{BASE}/mcp-catalogue")).json())["github"]
    assert admin_view["can"]["install"] is True and admin_view["cannot_reason"] is None

    await project_only(client, "owner@varga.dev")
    owner_view = _by_id((await client.get(f"{BASE}/mcp-catalogue")).json())["github"]
    assert owner_view["can"]["install"] is False
    assert "administrator" in owner_view["cannot_reason"]
    refused = await client.post(
        f"{BASE}/mcp-catalogue/github/install", json={"secret_refs": {"token": "env:CONNECTOR_RIGHTS_TEST"}}
    )
    assert refused.status_code == 403


async def test_an_entry_that_cannot_be_installed_says_why_even_to_the_administrator(
    client: AsyncClient, org: str
) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    notion = _by_id((await client.get(f"{BASE}/mcp-catalogue")).json())["notion"]
    assert notion["install"] == "needs_oauth"
    assert notion["can"]["install"] is False and "OAuth" in notion["cannot_reason"]


async def test_enable_projects_follow_the_caller(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    declared = await client.post(
        f"{BASE}/mcp-catalogue/github/install",
        json={"secret_refs": {"token": "env:CONNECTOR_RIGHTS_TEST"}, "availability": "selected_projects"},
    )
    assert declared.status_code == 201, declared.text

    async def enable_projects() -> list[str]:
        return _by_id((await client.get(f"{BASE}/mcp-catalogue")).json())["github"]["can"]["enable_projects"]

    assert await enable_projects() == ["billing", "checkout"]
    await project_only(client, "owner@varga.dev")
    assert await enable_projects() == ["billing"]
    await project_only(client, "dev@varga.dev")
    assert await enable_projects() == []


async def test_all_projects_connectors_offer_no_enablement(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    await client.post(
        f"{BASE}/mcp-catalogue/github/install", json={"secret_refs": {"token": "env:CONNECTOR_RIGHTS_TEST"}}
    )
    github = _by_id((await client.get(f"{BASE}/mcp-catalogue")).json())["github"]
    assert github["can"]["enable_projects"] == []
    assert github["enablement"] == [
        {"connector": "github", "availability": "all_projects", "enabled_projects": []}
    ]


async def test_the_connector_catalogue_lists_the_registry_by_family(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    body = (await client.get(f"{BASE}/connector-catalogue")).json()
    entries = {(e["kind"], e["type"]): e for e in body["entries"]}
    kinds = {kind for kind, _ in entries}
    assert not kinds & {"runtime", "gateway", "memory"}, "platform plumbing is not an entry"
    assert all(t != "fake" for _, t in entries)
    github = entries[("scm", "github")]
    assert (github["scope"], github["family"]) == ("project", "Source code")
    assert github["can"]["configure_projects"] == ["billing", "checkout"]
    assert github["can"]["install"] is False
    entra = entries[("identity", "entra")]
    assert (entra["scope"], entra["family"]) == ("organisation", "Directory")
    assert entra["can"]["install"] is True and entra["operations"] > 0
    assert entra["secret_fields"], "field names are listed, never values"
    assert entries[("documents", "sharepoint")]["family"] == "Documents"
    assert entries[("cd", "argocd")]["family"] == "Deployment"
    for kind, type_name in (("firewall", "pfsense"), ("network_policy", "cilium"), ("web_server", "nginx")):
        assert entries[(kind, type_name)]["family"] == "Network operations"
    assert body["entries"][0]["family"] == "Source code", "families come in the console's order"


async def test_the_connector_catalogue_filters(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    only_org = (await client.get(f"{BASE}/connector-catalogue", params={"scope": "organisation"})).json()
    assert only_org["entries"] and {e["scope"] for e in only_org["entries"]} == {"organisation"}
    docs = (await client.get(f"{BASE}/connector-catalogue", params={"family": "Documents"})).json()
    assert {e["kind"] for e in docs["entries"]} == {"documents"}
    by_text = (await client.get(f"{BASE}/connector-catalogue", params={"q": "ENTRA"})).json()
    assert [e["type"] for e in by_text["entries"]] == ["entra"]


async def test_configured_in_lists_only_projects_the_caller_sees(client: AsyncClient, org: str) -> None:
    await _world(client, org)
    await as_user(client, "admin@varga.dev")
    project = (await client.get(f"{BASE}/projects")).json()["items"]
    configured = {p["slug"]: p for p in project}
    assert configured, "the helper created projects"
    from choregos_api.db.models import Connector, Project
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        for slug in ("billing", "checkout"):
            pid = (await session.execute(select(Project.id).where(Project.slug == slug))).scalar_one()
            session.add(Connector(project_id=pid, kind="scm", type="github", config={}))

    def configured_in(body: dict[str, Any]) -> list[str]:
        return next(e for e in body["entries"] if (e["kind"], e["type"]) == ("scm", "github"))[
            "configured_in"
        ]

    assert configured_in((await client.get(f"{BASE}/connector-catalogue")).json()) == ["billing", "checkout"]
    await project_only(client, "owner@varga.dev")
    assert configured_in((await client.get(f"{BASE}/connector-catalogue")).json()) == ["billing"]
