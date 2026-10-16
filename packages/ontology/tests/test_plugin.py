# SPDX-License-Identifier: Apache-2.0
"""The ontology plugged into the Choregos core as a plugin (trial, items 2 and 3).

The plugin is installed as `pip` would: a `.dist-info` that declares its two entry points, found by
`importlib.metadata`. `create_app()` loads it — not the test — and `python -m choregos_api.migrer`
plays its migration branch.

What these tests prove: uploading a package, the idempotent synchronisation of an observations NDJSON
v1 report (a partial report writes nothing), and reading the objects through the generated MCP tools,
with the run's token, through the `choregos-tools` MCP server the agent uses.
What they do not prove: that a model chooses these tools (no LLM in the loop), nor RLS on PostgreSQL
(`test_plugin_postgres.py`, run when a database is provided).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .helpers import (
    call_tool,
    create_project,
    create_run,
    line,
    package_files,
    post_report,
    report,
    sign_in,
    stored_objects,
)


@pytest.fixture
async def ontology(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    response = await client.put(f"/api/v1/projects/{project['id']}/ontology", json={"files": package_files()})
    assert response.status_code == 200, response.text
    return dict(response.json())


WEEK_1 = report(
    line("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    line("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    line("reboot-required", "node-3", "ok", observed_at="2026-10-03T08:00:02Z"),
    line("ntp-drift", "node-1", "finding", severity="low", value=2.5, observed_at="2026-10-03T08:00:03Z"),
    line("disk-usage", "node-1", "ok", value=41.5, observed_at="2026-10-03T08:00:04Z"),
)


# ───────────────────────────── the plugin in the core ─────────────────────────────


async def test_the_core_loads_the_plugin_and_serves_its_routes(app: Any) -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/projects/{id}/ontology" in paths
    assert "/api/v1/projects/{id}/observations" in paths
    assert "/api/v1/projects/{id}/objects/{object_type}" in paths


async def test_a_valid_package_becomes_the_active_version(
    client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any]
) -> None:
    assert ontology["name"] == "core-ref"
    assert ontology["mcp_tools"] == 15
    second = await client.put(f"/api/v1/projects/{project['id']}/ontology", json={"files": package_files()})
    assert second.status_code == 200, second.text
    active = await client.get(f"/api/v1/projects/{project['id']}/ontology")
    assert active.status_code == 200, active.text
    assert active.json()["id"] == second.json()["id"], "one active version only: the last one"
    assert "finding_search" in active.json()["mcp_tools"]


async def test_an_invalid_package_is_refused_with_its_errors_and_replaces_nothing(
    client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any]
) -> None:
    files = package_files()
    files["objects/host.yaml"] = files["objects/host.yaml"].replace("type: timestamp", "type: instant")
    refusal = await client.put(f"/api/v1/projects/{project['id']}/ontology", json={"files": files})
    assert refusal.status_code == 422, refusal.text
    errors = refusal.json()["errors"]
    assert errors and errors[0]["file"] == "objects/host.yaml"
    assert (await client.get(f"/api/v1/projects/{project['id']}/ontology")).json()["id"] == ontology["id"]


@pytest.mark.parametrize("name", ["../escape.yaml", "/etc/passwd.yaml", "objects/../../x.yaml", "notes.txt"])
async def test_a_file_name_outside_the_package_is_refused(
    client: AsyncClient, project: dict[str, Any], name: str
) -> None:
    files = {**package_files(), name: "apiVersion: x\n"}
    refusal = await client.put(f"/api/v1/projects/{project['id']}/ontology", json={"files": files})
    assert refusal.status_code == 422, refusal.text


# ───────────────────────────── item 2: synchronisation ─────────────────────────────


async def test_a_report_opens_the_findings_and_replaying_it_changes_nothing(
    client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any]
) -> None:
    first = await post_report(client, project, WEEK_1)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "lines": 5,
        "created": 2,
        "updated": 0,
        "resolved": 0,
        "unchanged": 0,
        "proposals_decided": [],
    }
    state = await stored_objects(project["id"])
    assert set(state) == {"os-reboot-required", "os-ntp-drift"}
    assert state["os-reboot-required"][1]["scopes"] == ["node-1", "node-2"]

    replay = await post_report(client, project, WEEK_1)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {
        "lines": 5,
        "created": 0,
        "updated": 0,
        "resolved": 0,
        "unchanged": 2,
        "proposals_decided": [],
    }
    assert await stored_objects(project["id"]) == state, "the replay wrote nothing, not even a row version"


@pytest.mark.parametrize(
    "text",
    [
        # Truncated: the `_end` line is missing, though the report would resolve and open findings.
        "\n".join(
            [
                json.dumps(line("reboot-required", "node-1", "ok")),
                json.dumps(line("reboot-required", "node-2", "ok")),
                json.dumps(line("selinux", "node-9", "finding")),
            ]
        ),
        report(line("reboot-required", "node-1", "ok"), end={"_end": True, "lines": 7}),
        report(line("reboot-required", "node-1", "ok"), line("selinux", "node-9", "maybe")),
    ],
    ids=["no-end", "wrong-count", "invalid-line"],
)
async def test_a_partial_report_writes_nothing(
    client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any], text: str
) -> None:
    assert (await post_report(client, project, WEEK_1)).status_code == 200
    before = await stored_objects(project["id"])
    refusal = await post_report(client, project, text)
    assert refusal.status_code == 422, refusal.text
    assert refusal.json()["errors"][0]["code"] == "observations_partial_read"
    assert await stored_objects(project["id"]) == before


async def test_an_ok_resolves_its_scope_and_unreachable_resolves_nothing(
    client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any]
) -> None:
    await post_report(client, project, WEEK_1)
    week_2 = report(
        line("reboot-required", "node-1", "ok", observed_at="2026-10-10T08:00:00Z"),
        line("reboot-required", "node-2", "unreachable", observed_at="2026-10-10T08:00:01Z"),
        line("ntp-drift", "node-1", "ok", observed_at="2026-10-10T08:00:02Z"),
    )
    response = await post_report(client, project, week_2)
    assert response.json() == {
        "lines": 3,
        "created": 0,
        "updated": 1,
        "resolved": 1,
        "unchanged": 0,
        "proposals_decided": [],
    }
    state = await stored_objects(project["id"])
    assert state["os-reboot-required"][1]["scopes"] == ["node-2"]
    assert state["os-reboot-required"][1]["status"] == "open"
    assert state["os-ntp-drift"][1]["status"] == "resolved"
    assert state["os-ntp-drift"][1]["resolved_at"] == "2026-10-10T08:00:02+00:00"


async def test_without_an_active_ontology_a_report_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    refusal = await post_report(client, project, WEEK_1)
    assert refusal.status_code == 409, refusal.text


async def test_a_reader_does_not_synchronise(
    app: Any, client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any]
) -> None:
    added = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "reader@varga.dev", "role": "viewer"}
    )
    assert added.status_code in {200, 201}, added.text
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as reader:
        await sign_in(reader, "reader@varga.dev")
        assert (await reader.get(f"/api/v1/projects/{project['id']}/objects/finding")).status_code == 200
        refusal = await post_report(reader, project, WEEK_1)
    assert refusal.status_code == 403, refusal.text
    assert await stored_objects(project["id"]) == {}


# ───────────────────────────── item 3: the tools, with the run's token ─────────────────────────────


async def _seed(project_id: str) -> None:
    """Hosts and services, put in the database as a synchronisation would (not written yet)."""
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject

    objects = [
        ("host", "node-1", {"id": "node-1", "name": "node-1", "os": "ubuntu", "ip": "10.0.0.11"}),
        ("host", "node-2", {"id": "node-2", "name": "node-2", "os": "debian", "ip": "10.0.0.12"}),
        ("service", "svc-a", {"id": "svc-a", "name": "api", "host_id": "node-1"}),
        ("service", "svc-b", {"id": "svc-b", "name": "worker", "host_id": "node-1"}),
        ("service", "svc-c", {"id": "svc-c", "name": "db", "host_id": "node-2"}),
    ]
    async with session_scope() as session:
        for type_, ident, properties in objects:
            session.add(
                ManagedObject(project_id=project_id, object_type=type_, id=ident, properties=properties)
            )


@pytest.fixture
async def infra_run(client: AsyncClient, project: dict[str, Any], ontology: dict[str, Any]) -> dict[str, str]:
    assert (await post_report(client, project, WEEK_1)).status_code == 200
    await _seed(project["id"])
    return await create_run(project, "run-infra-1")


async def _call(http: AsyncClient, headers: dict[str, str], tool: str, arguments: dict[str, Any]) -> Any:
    return await call_tool(http, "run-infra-1", headers, tool, arguments)


async def test_the_run_sees_the_generated_tools_the_trial_can_serve(
    client: AsyncClient, infra_run: dict[str, str]
) -> None:
    response = await client.get("/api/v1/internal/runs/run-infra-1/tools", headers=infra_run)
    assert response.status_code == 200, response.text
    # `web_fetch` is the platform's own tool (S40-01f): offered to any run whose network allows a host.
    names = sorted(t["name"] for t in response.json()["tools"] if t["name"] != "web_fetch")
    assert names == [
        "action_list",
        "action_status",
        "finding_get",
        "finding_search",
        "host_get",
        "host_search",
        "host_services",
        "ontology_describe",
        "service_get",
        "service_host",
        "service_search",
    ], "no `alert_*` (connector), no `escalate_finding` (`notify`), no `open_finding` (not an agent)"
    assert all(set(t) == {"name", "description", "inputSchema"} for t in response.json()["tools"])


async def test_the_agent_reads_the_findings_and_a_host_through_the_tools(
    client: AsyncClient, infra_run: dict[str, str]
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    open_findings = await _call(
        client,
        infra_run,
        "finding_search",
        {"filters": [{"property": "severity", "op": "in", "value": ["high", "critical"]}]},
    )
    assert open_findings["status_code"] == 200
    assert [o["key"] for o in open_findings["result"]["objects"]] == ["os-reboot-required"]

    host = await _call(client, infra_run, "host_get", {"id": "node-1"})
    assert host["result"]["object"] == {"id": "node-1", "name": "node-1", "os": "ubuntu"}, (
        "`ip` is confidential"
    )

    services = await _call(client, infra_run, "host_services", {"id": "node-1"})
    assert [s["id"] for s in services["result"]["objects"]] == ["svc-a", "svc-b"]
    carrier = await _call(client, infra_run, "service_host", {"id": "svc-c"})
    assert [h["id"] for h in carrier["result"]["objects"]] == ["node-2"]

    async with session_scope() as session:
        calls = select(Event).where(Event.type == EventType.TOOL_CALLED.value).order_by(Event.ts)
        journal = [e.payload["tool"] for e in (await session.execute(calls)).scalars()]
        costs = (
            await session.execute(select(CostLedger).where(CostLedger.run_id == "run-infra-1"))
        ).scalars()
        providers = {c.provider for c in costs}
    assert journal == ["finding_search", "host_get", "host_services", "service_host"]
    assert providers == {"greffon:choregos-ontology"}  # lang: fr-ok - the provider name the core records


async def test_a_confidential_property_does_not_exist_in_filters(
    client: AsyncClient, infra_run: dict[str, str]
) -> None:
    refusal = await _call(
        client, infra_run, "host_search", {"filters": [{"property": "ip", "op": "prefix", "value": "10."}]}
    )
    assert refusal["status_code"] == 400, "otherwise the value could be inferred from a filter"


async def test_an_argument_outside_the_schema_is_refused(
    client: AsyncClient, infra_run: dict[str, str]
) -> None:
    refusal = await _call(client, infra_run, "finding_search", {"limit": 1000})
    assert refusal["status_code"] == 400
    assert "schema" in refusal["result"]["error"]


async def test_without_a_token_or_with_another_runs_token_the_tool_is_refused(
    client: AsyncClient, project: dict[str, Any], infra_run: dict[str, str]
) -> None:
    without = await client.post("/api/v1/internal/runs/run-infra-1/tools/host_get", json={"id": "node-1"})
    assert without.status_code == 401, without.text
    other = await create_run(project, "run-infra-2")
    crossed = await client.post(
        "/api/v1/internal/runs/run-infra-1/tools/host_get", headers=other, json={"id": "node-1"}
    )
    assert crossed.status_code == 403, crossed.text


async def test_the_run_of_another_project_sees_only_its_own_objects(
    client: AsyncClient, project: dict[str, Any], infra_run: dict[str, str]
) -> None:
    other = await create_project(client, "infra-b")
    assert (
        await client.put(f"/api/v1/projects/{other['id']}/ontology", json={"files": package_files()})
    ).status_code == 200
    alone = report(line("selinux", "node-b", "finding"))
    assert (await post_report(client, other, alone)).status_code == 200
    headers = await create_run(other, "run-b-1")
    response = await client.post(
        "/api/v1/internal/runs/run-b-1/tools/finding_search", headers=headers, json={"limit": 100}
    )
    assert [o["key"] for o in response.json()["result"]["objects"]] == ["os-selinux"]
    absent = await client.post(
        "/api/v1/internal/runs/run-b-1/tools/host_get", headers=headers, json={"id": "node-1"}
    )
    assert absent.json()["status_code"] == 404


async def test_through_the_agents_mcp_server(app: Any, infra_run: dict[str, str]) -> None:
    """The agent's path: `choregos-tools` (the runner's MCP server) relays to the internal API."""
    from choregos_tools_mcp.client import InternalClient
    from choregos_tools_mcp.server import McpServer

    internal = InternalClient("http://test/api/v1/internal", "run-infra-1", "unused")
    await internal.aclose()
    internal._client = AsyncClient(transport=ASGITransport(app=app), headers=infra_run)
    server = McpServer(internal)
    try:
        listed = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert listed is not None
        names = {t["name"] for t in listed["result"]["tools"]}
        assert {"finding_search", "host_get", "report_finding"} <= names, "its own AND the ontology's"
        call = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "host_get", "arguments": {"id": "node-2"}},
            }
        )
    finally:
        await internal.aclose()
    assert call is not None
    result = call["result"]
    assert not result.get("isError"), result
    text = result["content"][0]["text"]
    assert '"node-2"' in text
    assert "10.0.0.12" not in text


# ───────────────────────────── the migration branch ─────────────────────────────


def test_the_plugin_branch_creates_its_tables_and_goes_down_alone(
    plugin: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic.script import ScriptDirectory
    from choregos_api.config import reset_settings_cache
    from choregos_api.migrer import configuration, main
    from sqlalchemy import create_engine, inspect, text

    database = tmp_path / "migrated.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    reset_settings_cache()
    (core_head,) = ScriptDirectory.from_config(configuration(avec_les_greffons=False)).get_heads()
    engine = create_engine(f"sqlite:///{database}")

    def state() -> tuple[set[str], set[str]]:
        with engine.connect() as connection:
            versions = {r[0] for r in connection.execute(text("SELECT version_num FROM alembic_version"))}
        return set(inspect(engine).get_table_names()), versions

    try:
        main([])
        tables, versions = state()
        assert {"ontology_versions", "managed_objects", "actions", "projects"} <= tables
        assert "action_proposals" not in tables, "proposals are core actions (onto0003)"
        # The plugin's head depends on a core revision, not on its head: as soon as the core moves on
        # (scoped tokens, ADR 0030), both heads live side by side in `alembic_version`.
        assert "onto0003" in versions
        assert versions <= {"onto0003", core_head}
        main(["downgrade", "ontology@base"])
        tables, versions = state()
        assert not {"ontology_versions", "managed_objects", "action_proposals"} & tables
        assert versions == {core_head}
    finally:
        engine.dispose()
        reset_settings_cache()


def _proposal_row(ident: str, status: str) -> dict[str, Any]:
    import json

    return {
        "id": ident,
        "status": status,
        "targets": json.dumps(["os-key"]),
        "params": json.dumps({"title": "t"}),
        "by": json.dumps({"kind": "agent", "id": "agent:platform", "run_id": "r1"}),
        "approval": json.dumps({"approvers": [{"role": "owner"}], "step_up_minutes": 10}),
    }


INSERT_A_PROPOSAL = (
    "INSERT INTO action_proposals (id, project_id, version_id, action_type, target_ids, params, "
    "justification, status, proposed_by, approval, decisions, effects, evidence, created_at, updated_at) "
    "VALUES (:id, 'p1', 'v1', 'open_infra_pr', :targets, :params, 'two nodes', :status, :by, :approval, "
    "'[]', '[]', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
)


def test_onto0003_copies_the_proposals_into_the_actions_then_drops_the_table(
    plugin: None, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending proposal becomes a core action, under the same id, with its core effects derived from its
    version — it will be decided and executed like the others; a proposal that was running in the request
    is closed, and says so (S20-08)."""
    import json

    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization, Project
    from choregos_api.migrer import main
    from choregos_ontology.service.store import OntologyVersion
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import Session

    database = tmp_path / "onto0003.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    reset_settings_cache()
    engine = create_engine(f"sqlite:///{database}")
    effects = [{"type": "gitops.pull_request"}]
    ir = {"action_types": [{"name": "open_infra_pr", "effects": effects, "evidence": [{"name": "pr_open"}]}]}
    try:
        main(["upgrade", "f8a0b2c4d6e9"])
        main(["upgrade", "onto0002"])
        with Session(engine) as session:
            session.add(Organization(id="o1", slug="varga", name="Varga"))
            session.add(Project(id="p1", org_id="o1", slug="infra", name="Infra", status="active", config={}))
            session.flush()
            session.add(
                OntologyVersion(
                    id="v1", project_id="p1", name="it4it", version="1", checksum="sha", compiled_ir=ir
                )
            )
            session.commit()
        with engine.begin() as connection:
            for ident, status in (("a1", "pending_approval"), ("a2", "running")):
                connection.execute(text(INSERT_A_PROPOSAL), _proposal_row(ident, status))
        main([])
        assert "action_proposals" not in set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            rows = {r["id"]: r for r in connection.execute(text("SELECT * FROM actions")).mappings()}
        pending, interrupted = rows["a1"], rows["a2"]
        assert (pending["origin"], pending["kind"]) == ("ontology", "ontology.open_infra_pr")
        assert pending["status"] == "pending_approval"
        assert json.loads(pending["effects"]) == [
            {"effect": "ontology.effet", "with": {"index": 0}},  # lang: fr-ok - persisted effect kind
            {"effect": "ontology.preuve", "with": {"index": 0, "position": 1}},  # lang: fr-ok
        ]
        assert json.loads(pending["approval"])["approvers"] == [{"role": "project_owner", "min": 1}]
        meta = json.loads(pending["params"])["ontologie"]  # lang: fr-ok - persisted key
        assert (meta["version_id"], meta["target_ids"], meta["params"]) == ("v1", ["os-key"], {"title": "t"})
        assert pending["run_id"] == "r1" and pending["org_id"] == "o1"
        assert interrupted["status"] == "failed" and "onto0003" in interrupted["error"]
    finally:
        engine.dispose()
        reset_settings_cache()


# ───────────────────────────── inactive by default ─────────────────────────────


async def test_without_activation_the_plugin_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shipped in every image, the plugin does nothing until `CHOREGOS_ESSAI_ONTOLOGIE` is set."""
    import os

    from choregos_api.greffons import reinitialiser, routeurs_declares
    from choregos_api.main import create_app
    from choregos_ontology.service.plugin import MIGRATIONS

    monkeypatch.delenv("CHOREGOS_ESSAI_ONTOLOGIE", raising=False)
    reinitialiser()
    try:
        paths = create_app().openapi()["paths"]
        assert "/api/v1/projects/{id}/ontology" not in paths
        assert routeurs_declares() == ()
        assert os.fspath(MIGRATIONS).endswith("inactive"), "inactive, the migration branch is empty"
        monkeypatch.setenv("CHOREGOS_ESSAI_ONTOLOGIE", "1")
        assert os.fspath(MIGRATIONS).endswith("versions")
    finally:
        reinitialiser()


def test_without_activation_no_plugin_table(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.migrer import main
    from sqlalchemy import create_engine, inspect

    monkeypatch.delenv("CHOREGOS_ESSAI_ONTOLOGIE", raising=False)
    database = tmp_path / "without-plugin.db"
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    reset_settings_cache()
    engine = create_engine(f"sqlite:///{database}")
    try:
        main([])
        tables = set(inspect(engine).get_table_names())
        assert "projects" in tables
        assert not {"ontology_versions", "managed_objects", "action_proposals"} & tables
    finally:
        engine.dispose()
        reset_settings_cache()
