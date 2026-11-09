"""What an agent version reaches is closed by what it names, and read in one place (S60-02, ADR 0127).

A source whose list is present on the version is closed: the run is offered what the version names AND
what its project allows. An absent list keeps the project's behaviour (a version published before the
ADR runs as it did). The run's tool listing, its call and `GET /agents/{slug}/access` read the same
function, so the console cannot show what a run would not get.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from . import test_extension_tools as _installed
from .kits.tool_catalogue import intercept_host

pytestmark = pytest.mark.asyncio

#: The installed extension (a fixture of its own file); a test asks for it BEFORE `client`.
extension_tools = _installed.extension_tools

AGENTS = "/api/v1/orgs/varga/agents"
CATALOGUE = """
tools:
  - name: lookup
    description: Looks a record up.
    provider: directory
    http: { method: GET, url: "https://api.directory.example/v1/lookup" }
  - name: search_company
    description: Searches a company.
    provider: directory
    groups: [rh]
    http: { method: GET, url: "https://api.directory.example/v1/company" }
  - name: audit_log
    description: Reads the audit log.
    provider: directory
    groups: [rh]
    http: { method: GET, url: "https://api.directory.example/v1/audit" }
  - name: pay
    description: Pays an invoice.
    provider: billing
    http: { method: POST, url: "https://api.billing.example/v1/pay" }
  - name: payroll
    description: Reads the payroll.
    provider: directory
    groups: [finance]
    http: { method: GET, url: "https://api.directory.example/v1/payroll" }
"""
#: The project opens these; `audit_log` is in the group `rh` the project belongs to but is not opened,
#: `payroll` is reserved for `finance`, `pay` may write.
OPENED = ["lookup", "search_company", "pay", "payroll"]
FRAME = {"validate_result", "report_finding", "ask_human"}


@pytest.fixture
async def catalogue(
    project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """The deployment's catalogue, opened to the project as `OPENED` and the group `rh`."""
    from choregos_api import catalogue as catalogue_service
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    file = tmp_path / "catalogue.yaml"
    file.write_text(CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(file))
    catalogue_service.clear_cache()
    async with session_scope() as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        row.config = {**(row.config or {}), "tools": OPENED, "groups": ["rh"]}
    yield project
    catalogue_service.clear_cache()


@pytest.fixture
def web_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's network allows a host and a search backend serves the project."""
    from choregos_api.services import web_fetch, web_search

    async def yes(*args: Any, **kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(web_fetch, "offered", yes)
    monkeypatch.setattr(web_search, "offered", yes)


async def _agent(client: AsyncClient, slug: str, **spec: Any) -> None:
    created = await client.post(AGENTS, json={"slug": slug, "display_name": slug, "spec": spec})
    assert created.status_code == 201, created.text


_n = [0]


async def _run(project: dict[str, Any], slug: str) -> tuple[str, dict[str, str]]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    _n[0] += 1
    run_id = f"run-{slug}-{_n[0]}"
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key=f"varga/x#{run_id}", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="custom",
                status="running",
                agent_slug=slug,
                agent_version=1,
            )
        )
    token = mint_run_token(
        run_id, project_slug="billing-api", work_item_key=f"varga/x#{run_id}", ttl_minutes=30
    )
    return run_id, {"Authorization": f"Bearer {token}"}


async def _listed(client: AsyncClient, project: dict[str, Any], slug: str) -> set[str]:
    run_id, headers = await _run(project, slug)
    response = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert response.status_code == 200, response.text
    return {t["name"] for t in response.json()["tools"]}


async def _call(client: AsyncClient, project: dict[str, Any], slug: str, tool: str) -> int:
    run_id, headers = await _run(project, slug)
    called = await client.post(f"/api/v1/internal/runs/{run_id}/tools/{tool}", headers=headers, json={})
    return called.status_code


# ───────────────────────── the run's listing ─────────────────────────


async def test_a_version_without_lists_keeps_what_the_project_gives(
    extension_tools: None, client: AsyncClient, catalogue: dict[str, Any], web_everywhere: None
) -> None:
    await _agent(client, "legacy", model="m")
    assert await _listed(client, catalogue, "legacy") == {
        "lookup",
        "search_company",
        "internal_directory",
        "web_fetch",
        "web_search",
    }


async def test_a_closed_catalogue_offers_what_is_named_and_what_the_project_opens(
    extension_tools: None, client: AsyncClient, catalogue: dict[str, Any], web_everywhere: None
) -> None:
    # `group:rh` expands to the catalogue's `rh` tools; `audit_log` is one of them but the project does
    # not open it, `payroll` is reserved for another group, `pay` may write, `ghost` does not exist.
    await _agent(
        client,
        "closed",
        tools={
            "catalogue": ["lookup", "group:rh", "payroll", "pay", "ghost"],
            "extensions": [],
            "web": [],
        },
    )
    assert await _listed(client, catalogue, "closed") == {"lookup", "search_company"}


async def test_a_closed_catalogue_names_one_tool_and_leaves_the_rest_to_nobody(
    client: AsyncClient, catalogue: dict[str, Any]
) -> None:
    await _agent(client, "narrow", tools={"catalogue": ["search_company"], "web": []})
    assert await _listed(client, catalogue, "narrow") == {"search_company"}
    await _agent(client, "none", tools={"catalogue": [], "web": []})
    assert await _listed(client, catalogue, "none") == set(), "present and empty closes the source"


async def test_a_closed_extensions_list_offers_only_the_extensions_it_names(
    extension_tools: None, client: AsyncClient, catalogue: dict[str, Any]
) -> None:
    await _agent(client, "no-ext", tools={"extensions": []})
    assert "internal_directory" not in await _listed(client, catalogue, "no-ext")
    await _agent(client, "other-ext", tools={"extensions": ["somewhere_else"]})
    assert "internal_directory" not in await _listed(client, catalogue, "other-ext")
    await _agent(client, "ext", tools={"extensions": ["extension_tools"]})
    listed = await _listed(client, catalogue, "ext")
    assert "internal_directory" in listed and "lookup" in listed, "the catalogue was left open"


async def test_a_closed_web_list_offers_only_the_web_tools_it_names(
    client: AsyncClient, catalogue: dict[str, Any], web_everywhere: None
) -> None:
    await _agent(client, "fetcher", tools={"web": ["fetch"]})
    listed = await _listed(client, catalogue, "fetcher")
    assert {"web_fetch", "web_search"} & listed == {"web_fetch"}
    await _agent(client, "offline", tools={"web": []})
    assert not {"web_fetch", "web_search"} & await _listed(client, catalogue, "offline")


async def test_a_web_tool_the_network_cannot_serve_is_not_offered_even_when_named(
    client: AsyncClient, catalogue: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import web_fetch

    async def no(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(web_fetch, "offered", no)
    await _agent(client, "fetcher", tools={"web": ["fetch"]})
    assert "web_fetch" not in await _listed(client, catalogue, "fetcher")


# ───────────────────────── the run's call ─────────────────────────


async def test_a_call_to_a_tool_the_version_does_not_name_is_a_404_like_an_unknown_one(
    extension_tools: None,
    client: AsyncClient,
    catalogue: dict[str, Any],
    web_everywhere: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []
    intercept_host(monkeypatch, "https://api.directory.example", httpx.Response(200, json={"ok": 1}), seen)
    await _agent(client, "closed", tools={"catalogue": ["lookup"], "extensions": [], "web": []})
    await _agent(client, "legacy", model="m")
    # The project opens `search_company`, the extension and the web: the version closed them.
    for tool in ("search_company", "internal_directory", "web_fetch", "web_search", "ghost"):
        assert await _call(client, catalogue, "closed", tool) == 404, tool
    assert await _call(client, catalogue, "closed", "lookup") == 200
    # The same calls on a version with no list are served as before.
    assert await _call(client, catalogue, "legacy", "search_company") == 200
    assert await _call(client, catalogue, "legacy", "internal_directory") == 200


# ───────────────────────── GET /agents/{slug}/access ─────────────────────────


def _by_name(access: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["name"]: t for t in access["tools"]}


async def test_the_access_of_a_legacy_version_says_what_it_inherits(
    extension_tools: None, client: AsyncClient, catalogue: dict[str, Any], web_everywhere: None
) -> None:
    await _agent(client, "legacy", instructions="Work.")
    response = await client.get(f"{AGENTS}/legacy/access", params={"project": "billing-api"})
    assert response.status_code == 200, response.text
    access = response.json()
    assert (access["agent"], access["version"], access["project"]) == ("legacy", 1, "billing-api")
    assert access["complete"] is False
    assert access["inherits"] == ["model", "tools", "memory"]
    assert access["model"]["origin"] == "project" and access["model"]["requested"] == "profile:by_size"
    tools = _by_name(access)
    assert {n for n, t in tools.items() if t["origin"] == "frame"} >= FRAME
    assert all(tools[n]["source"] == "frame" for n in FRAME)
    for name, source in (
        ("lookup", "catalogue"),
        ("search_company", "catalogue"),
        ("internal_directory", "extension"),
        ("web_fetch", "web"),
        ("web_search", "web"),
    ):
        assert (tools[name]["origin"], tools[name]["source"]) == ("project", source), name
    assert tools["internal_directory"]["access"] == "write"
    assert not [t for t in access["tools"] if t["origin"] == "refused"], (
        "a version that names nothing is refused nothing"
    )
    assert [(m["name"], m["origin"]) for m in access["memories"]] == [("project:shared", "project")]


async def test_the_access_of_a_complete_version_names_origins_and_refusals(
    extension_tools: None, client: AsyncClient, catalogue: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import web_fetch, web_search

    async def no(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(web_fetch, "offered", no)
    monkeypatch.setattr(web_search, "offered", no)
    await _agent(
        client,
        "complete",
        model="profile:standard",
        memories=[],
        tools={
            "catalogue": ["lookup", "group:rh", "payroll", "pay", "ghost", "group:nobody"],
            "extensions": ["extension_tools", "absent_ext"],
            "web": ["fetch"],
        },
    )
    access = (await client.get(f"{AGENTS}/complete/access", params={"project": "billing-api"})).json()
    assert access["complete"] is True and access["inherits"] == []
    assert access["model"]["origin"] == "agent" and access["model"]["requested"] == "profile:standard"
    assert access["memories"] == []
    tools = _by_name(access)
    assert [tools[n]["origin"] for n in ("lookup", "search_company", "internal_directory")] == ["agent"] * 3
    refused = {n: t["reason"] for n, t in tools.items() if t["origin"] == "refused"}
    assert set(refused) == {"audit_log", "payroll", "pay", "ghost", "group:nobody", "absent_ext", "web_fetch"}
    assert "does not open it" in refused["audit_log"]
    assert "reserved for groups" in refused["payroll"]
    assert "may write" in refused["pay"]
    assert "not in the deployment catalogue" in refused["ghost"]
    assert "no tool" in refused["group:nobody"]
    assert "no such extension" in refused["absent_ext"]
    assert "allows no host" in refused["web_fetch"]
    assert "web_search" not in tools, "a source the version did not name is not listed as refused"


async def test_the_access_and_the_run_read_the_same_tools(
    extension_tools: None, client: AsyncClient, catalogue: dict[str, Any], web_everywhere: None
) -> None:
    for slug, tools in (
        ("same-closed", {"catalogue": ["group:rh", "lookup"], "extensions": [], "web": ["search"]}),
        ("same-open", {"scripts": "off"}),
    ):
        await _agent(client, slug, tools=tools)
        access = (await client.get(f"{AGENTS}/{slug}/access", params={"project": "billing-api"})).json()
        offered = {t["name"] for t in access["tools"] if t["origin"] not in {"frame", "refused"}}
        assert offered == await _listed(client, catalogue, slug), slug


async def test_the_access_of_a_remote_and_a_voice_version(
    client: AsyncClient, catalogue: dict[str, Any]
) -> None:
    created = await client.post(
        AGENTS,
        json={
            "slug": "remote",
            "kind": "remote",
            "display_name": "Remote",
            "spec": {"runtime": {"kind": "a2a", "card_url": "https://rovo.example/.well-known/agent.json"}},
        },
    )
    if created.status_code == 201:  # the card is fetched at creation: a deployment without it is not our case
        access = (await client.get(f"{AGENTS}/remote/access", params={"project": "billing-api"})).json()
        assert access["complete"] is True and access["inherits"] == [] and access["tools"] == []


async def test_the_access_of_an_explicit_version_and_its_refusals(
    client: AsyncClient, catalogue: dict[str, Any]
) -> None:
    await _agent(client, "legacy", instructions="Work.")
    explicit = await client.get(f"{AGENTS}/legacy/access", params={"project": "billing-api", "version": 1})
    assert explicit.status_code == 200 and explicit.json()["version"] == 1
    assert (
        await client.get(f"{AGENTS}/legacy/access", params={"project": "billing-api", "version": 9})
    ).status_code == 404
    assert (await client.get(f"{AGENTS}/legacy/access", params={"project": "nowhere"})).status_code == 404
    assert (await client.get(f"{AGENTS}/ghost/access", params={"project": "billing-api"})).status_code == 404
    assert (await client.get(f"{AGENTS}/legacy/access")).status_code == 422, "the project is required"
