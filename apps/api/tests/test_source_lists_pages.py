"""The lists that are not SQL tables are paged by the server too (ADR 0206).

`listTriggers`, `listWorkflowCatalogue`, `listAgentCatalogue`, `listPlatformModels` and
`listUsableModels` read their rows from a project's configuration, from files shipped with the
platform, or from the model gateway. Each is built as it always was (same order, same permission and
policy cuts), filtered on `q` and the route's own parameters, then cut by position: the cost is the
build, the payload is one page.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core import GatewayModel

from .conftest import login

ORG = "/api/v1/orgs/northwind"
TRIGGERS = [{"name": f"hook-{index:02d}", "title": f"Title {index}", "workflow": None} for index in range(7)]
MODELS = [
    GatewayModel(model_name=f"acme/model-{index:02d}", litellm_model=f"up/m-{index}", provider="acme")
    for index in range(5)
] + [GatewayModel(model_name="other/special", litellm_model="x/special", provider="zeta")]


async def _pages(client: AsyncClient, url: str, key: Any, **params: Any) -> list[Any]:
    """Every row of a list, page after page, by the cursor each page hands back."""
    seen: list[Any] = []
    cursor: str | None = None
    for _ in range(300):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "meta"}
        seen += [key(item) for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]
        assert cursor
    raise AssertionError("the list never ended")


async def _refuses_a_bad_page(client: AsyncClient, url: str) -> None:
    assert (await client.get(url, params={"cursor": "not-a-cursor"})).status_code == 422
    assert (await client.get(url, params={"limit": 0})).status_code == 422
    assert (await client.get(url, params={"limit": 201})).status_code == 422


async def _project(client: AsyncClient) -> dict[str, Any]:
    created = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "hooks",
            "name": "hooks",
            "config": {"slug": "hooks", "org": "northwind", "triggers": TRIGGERS},
        },
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_the_triggers_are_paged_in_the_order_the_configuration_declares_them(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    url = f"/api/v1/projects/{project['id']}/triggers"
    names = [t["name"] for t in TRIGGERS]
    first = (await client.get(url, params={"limit": 3})).json()
    assert [t["name"] for t in first["items"]] == names[:3] and first["meta"]["has_more"] is True
    for limit in (1, 3, 200):
        assert await _pages(client, url, lambda t: t["name"], limit=limit) == names, "each once, in order"
    assert await _pages(client, url, lambda t: t["name"], q="HOOK-03") == ["hook-03"], "q reads the name"
    assert await _pages(client, url, lambda t: t["name"], q="title 5", limit=1) == ["hook-05"], (
        "and the title"
    )
    assert await _pages(client, url, lambda t: t["name"], q="no-such-trigger") == []
    await _refuses_a_bad_page(client, url)


async def test_the_workflow_library_is_paged_and_searched_by_the_server(
    client: AsyncClient, admin: str
) -> None:
    url = f"{ORG}/workflow-catalogue"
    whole = (await client.get(url, params={"limit": 200})).json()["items"]
    keys = [(e["source"], e["name"]) for e in whole]
    assert len(keys) > 3 and len(keys) == len(set(keys))
    assert all(e["yaml"] is None for e in whole)
    for limit in (1, 4):
        seen = await _pages(client, url, lambda e: (e["source"], e["name"]), limit=limit)
        assert seen == keys, "every entry once, in the library's order, whatever the page size"

    page = (await client.get(url, params={"limit": 2})).json()
    assert len(page["items"]) == 2 and page["meta"]["has_more"] is True
    assert {a["slug"] for e in page["items"] for a in e["agents"]} <= {
        a["slug"] for e in whole for a in e["agents"]
    }

    hr = await _pages(client, url, lambda e: e["name"], q="JOINERS")
    assert hr and all(
        "joiners" in " ".join([e["name"], e["title"], e["summary"], e["source"], *e["tags"]]).lower()
        for e in whole
        if e["name"] in hr
    )
    assert set(hr) < {e["name"] for e in whole}, "q narrows the library before the cut"
    without = await _pages(client, url, lambda e: (e["source"], e["name"]), needs_workspace="false", limit=3)
    with_ws = await _pages(client, url, lambda e: (e["source"], e["name"]), needs_workspace="true", limit=3)
    assert without and with_ws and not set(without) & set(with_ws)
    assert set(without) | set(with_ws) == set(keys)
    assert without == [k for k, e in zip(keys, whole, strict=True) if not e["needs_workspace"]]
    await _refuses_a_bad_page(client, url)


async def test_the_library_keeps_its_membership_cut(client: AsyncClient, admin: str) -> None:
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="acme", name="Acme"))
    assert (await client.get("/api/v1/orgs/acme/workflow-catalogue", params={"limit": 5})).status_code == 403
    assert (await client.get("/api/v1/orgs/acme/agent-catalogue", params={"limit": 5})).status_code == 403
    await client.post(f"{ORG}/members", json={"email": "po@northwind.dev", "role": "project_owner"})
    await login(client, "po@northwind.dev")
    assert (await client.get(f"{ORG}/workflow-catalogue", params={"limit": 5})).status_code == 200


async def test_the_agent_catalogue_is_paged_and_filtered_on_kind_and_q(
    client: AsyncClient, admin: str
) -> None:
    url = f"{ORG}/agent-catalogue"
    whole = (await client.get(url, params={"limit": 200})).json()["items"]
    slugs = [e["slug"] for e in whole]
    assert len(slugs) > 3 and len(slugs) == len(set(slugs))
    for limit in (1, 3):
        assert await _pages(client, url, lambda e: e["slug"], limit=limit) == slugs
    internal = await _pages(client, url, lambda e: e["slug"], kind="internal", limit=2)
    external = await _pages(client, url, lambda e: e["slug"], kind="external", limit=2)
    assert internal == [e["slug"] for e in whole if e["kind"] == "internal"]
    assert external == [e["slug"] for e in whole if e["kind"] == "external"]
    assert internal and external and not set(internal) & set(external)
    assert (await client.get(url, params={"kind": "bogus"})).status_code == 422
    found = await _pages(client, url, lambda e: e["slug"], q="DEVELOPER")
    assert "developer" in found and set(found) < set(slugs)
    assert await _pages(client, url, lambda e: e["slug"], q="no-such-agent-anywhere") == []
    page = (await client.get(url, params={"limit": 2})).json()["items"]
    assert [e["slug"] for e in page] == slugs[:2]
    assert all("installed" in e and "offered" in e for e in page), "the state is read for the page's rows"
    await _refuses_a_bad_page(client, url)


class _Gateway:
    capabilities = frozenset({"list_models"})

    async def list_models(self) -> list[GatewayModel]:
        return list(MODELS)


async def test_the_platform_models_are_paged_in_the_gateways_order(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_adapters import register, registry

    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_SPECS", dict(registry._SPECS))
    register("gateway", "scripted-test")(lambda cfg: _Gateway())
    monkeypatch.setenv("TRANIBOR_FAKES", "")
    monkeypatch.setenv("TRANIBOR_GATEWAY_TYPE", "scripted-test")
    url = "/api/v1/platform/models"
    names = [m.model_name for m in MODELS]
    for limit in (1, 4, 200):
        assert await _pages(client, url, lambda m: m["model_name"], limit=limit) == names
    assert await _pages(client, url, lambda m: m["model_name"], q="ZETA") == ["other/special"], "provider"
    assert await _pages(client, url, lambda m: m["model_name"], q="up/m-2") == ["acme/model-02"], "upstream"
    assert await _pages(client, url, lambda m: m["model_name"], q="nothing") == []
    await _refuses_a_bad_page(client, url)


async def test_the_usable_models_are_paged_by_name(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _Gateway()

    async def org_gateway(session: Any, org_id: str) -> _Gateway:
        return gateway

    monkeypatch.setattr("tranibor_api.routers.model_catalogue.org_gateway", org_gateway)
    url = f"{ORG}/models/usable"
    names = sorted(m.model_name for m in MODELS)
    for limit in (1, 4, 200):
        assert await _pages(client, url, lambda m: m["model_name"], limit=limit) == names
    assert await _pages(client, url, lambda m: m["model_name"], q="zeta", limit=1) == ["other/special"]
    assert await _pages(client, url, lambda m: m["model_name"], q="MODEL-0", limit=2) == [
        n for n in names if "model-0" in n
    ]
    await _refuses_a_bad_page(client, url)
