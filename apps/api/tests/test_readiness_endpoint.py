"""`GET /projects/{id}/readiness` on a project born from a template (S40-06)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


async def _project(client: AsyncClient) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "dev",
            "name": "Development",
            "template_ref": "github-software-delivery@1.0.0",
            "config": {
                "slug": "dev",
                "org": "varga",
                "repo": {"url": "https://github.com/varga/sandbox.git", "default_branch": "main"},
            },
        },
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _activate(project_id: str) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        row = await session.get(Project, project_id)
        assert row is not None
        row.status = "active"


async def _forget_the_tests(project_id: str) -> None:
    """Saving a connector tests it (S40-05): put the cards back to "never tested" to read that state."""
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        for row in (
            await session.execute(select(Connector).where(Connector.project_id == project_id))
        ).scalars():
            row.status = "unknown"
            row.last_check_at = None


async def _readiness(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/v1/projects/{project_id}/readiness")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _by_state(body: dict[str, Any], state: str) -> set[str]:
    return {i["id"] for i in body["items"] if i["state"] == state}


async def test_a_project_with_only_a_tracker_and_a_repository_is_not_ready(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    await _activate(project["id"])
    for kind, type_, config in (
        ("tracker", "github-issues", {"repo": "varga/sandbox"}),
        ("scm", "github", {"repo": "varga/sandbox"}),
    ):
        put = await client.put(
            f"/api/v1/projects/{project['id']}/connectors/{kind}", json={"type": type_, "config": config}
        )
        assert put.status_code == 200, put.text
    body = await _readiness(client, project["id"])
    assert body["ready"] is False
    assert _by_state(body, "todo") == {"connector-cd", "notifications", "people-release-captain"}, body[
        "items"
    ]
    assert {"connector-tracker", "connector-scm"} <= _by_state(body, "done"), (
        "tested by the save itself (S40-05)"
    )
    assert "connector-runtime" in _by_state(body, "done"), "provided by the deployment"
    assert body["summary"]["todo"] == 3
    assert body["summary"]["done"] + body["summary"]["check"] + body["summary"]["todo"] == len(
        [i for i in body["items"] if i["state"] != "not_needed"]
    )
    assert "repository-checks" in _by_state(body, "check"), "no test command"
    assert all(i["fix"]["by"] for i in body["items"])
    await _forget_the_tests(project["id"])
    forgotten = await _readiness(client, project["id"])
    assert {"connector-tracker", "connector-scm"} <= _by_state(forgotten, "check"), "configured, never tested"


async def test_configuring_cd_and_notify_moves_the_items(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    await _activate(project["id"])
    pid = project["id"]
    base = f"/api/v1/projects/{pid}"
    await client.put(f"{base}/connectors/cd", json={"type": "demo", "config": {}})
    await client.put(f"{base}/connectors/notify", json={"type": "slack", "config": {}})
    body = await _readiness(client, pid)
    assert "connector-cd" in _by_state(body, "check"), "a demo stand-in is to check, not done"
    assert "connector-cd" not in _by_state(body, "todo")
    assert "notifications" in _by_state(body, "todo"), "a connector but no channel"

    tested = await client.post(f"{base}/connectors/cd/test")
    assert tested.status_code == 200
    config = (await client.get(base)).json()["config"]
    patched = await client.patch(base, json={"config": {**config, "notify": {"slack_channel": "#choregos"}}})
    assert patched.status_code == 200, patched.text
    body = await _readiness(client, pid)
    assert "notifications" not in _by_state(body, "todo")
    members = await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "rc@varga.dev", "role": "release_captain", "project_slug": "dev"},
    )
    assert members.status_code == 201, members.text
    body = await _readiness(client, pid)
    assert "people-release-captain" in _by_state(body, "done")


async def test_a_viewer_reads_it_and_it_is_visible_to_no_more_than_the_project(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client)
    pid = project["id"]
    added = await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "viewer@varga.dev", "role": "viewer", "project_slug": "dev"},
    )
    assert added.status_code == 201, added.text
    client.cookies.clear()
    await login(client, "viewer@varga.dev")
    assert (await client.get(f"/api/v1/projects/{pid}/readiness")).status_code == 200, "project:read suffices"
    client.cookies.clear()
    await login(client, "stranger@elsewhere.dev")
    denied = await client.get(f"/api/v1/projects/{pid}/readiness")
    # Whoever the project route hides it from (another organisation) is refused here too: the
    # readiness shows no more than `GET /projects/{id}` does.
    assert denied.status_code == (await client.get(f"/api/v1/projects/{pid}")).status_code
    client.cookies.clear()
    assert (await client.get(f"/api/v1/projects/{pid}/readiness")).status_code == 401


async def test_a_github_owner_without_an_installation_is_to_do(
    client: AsyncClient, admin: str, github: Any
) -> None:
    project = await _project(client)
    pid = project["id"]
    github.add_installation(1, "varga")
    assert (
        await client.put(
            f"/api/v1/projects/{pid}/connectors/scm",
            json={"type": "github", "config": {"repo": "varga/sandbox"}},
        )
    ).status_code == 200
    body = await _readiness(client, pid)
    assert "github-app-scm" in _by_state(body, "done")
    github.remove_installation(1)
    github.add_installation(2, "someone-else")
    from choregos_api.db.models import GitHubInstallation
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    async with session_scope(orgs="*") as session:
        await session.execute(delete(GitHubInstallation))
    body = await _readiness(client, pid)
    assert "github-app-scm" in _by_state(body, "todo")


async def test_readiness_says_a_gvisor_policy_the_warm_pool_cannot_run(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S38-13d / #750: before any run, the item names the policy's runtime, the pool's, and the fix."""
    project = await _project(client)
    await _activate(project["id"])
    policy = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()["yaml"]
    assert "runtime: default" in policy
    edited = policy.replace("runtime: default", "runtime: gvisor", 1)
    put = await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": edited})
    assert put.status_code == 200, put.text
    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "agent_sandbox")
    monkeypatch.setenv("CHOREGOS_RUNNER_RUNTIME_CLASS", "")
    item = next(i for i in (await _readiness(client, project["id"]))["items"] if i["id"] == "runtime-class")
    assert item["state"] == "todo"
    assert "gvisor" in item["detail"] and "the cluster default" in item["detail"]
    assert "runner.runtimeClass" in item["detail"]
    monkeypatch.setenv("CHOREGOS_RUNNER_RUNTIME_CLASS", "gvisor")
    item = next(i for i in (await _readiness(client, project["id"]))["items"] if i["id"] == "runtime-class")
    assert item["state"] == "done"
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert "runtime-class" not in {i["id"] for i in (await _readiness(client, project["id"]))["items"]}


async def test_the_probe_of_the_object_store_is_cached_and_reports_its_failure() -> None:
    """A store that fails is named; the answer is reused for a minute; the fake answers."""
    from choregos_adapters.fakes import FakeObjectStore
    from choregos_api.object_store import use_object_store
    from choregos_api.services import readiness

    store = FakeObjectStore()
    use_object_store(store)
    readiness.forget_object_store_probe()
    try:
        store.fail_next("object store unreachable (https://s3.example): refused")
        failed = await readiness.probe_object_store()
        assert failed.error is not None and "unreachable" in failed.error
        assert (await readiness.probe_object_store()) is failed, "cached"
        readiness.forget_object_store_probe()
        assert (await readiness.probe_object_store()).error is None
        assert store.puts == 0 and not store.objects, "the probe creates nothing"
    finally:
        use_object_store(None)
        readiness.forget_object_store_probe()


async def test_readiness_of_a_project_that_writes_files_lists_the_object_store(
    client: AsyncClient, admin: str
) -> None:
    from choregos_adapters.fakes import FakeObjectStore
    from choregos_api.object_store import use_object_store
    from choregos_api.services import readiness

    store = FakeObjectStore()
    use_object_store(store)
    readiness.forget_object_store_probe()
    try:
        created = await client.post(
            "/api/v1/orgs/varga/projects",
            json={
                "slug": "quant",
                "name": "Quant",
                "template_ref": "investment-research@0.1.0",
                "config": {"slug": "quant", "org": "varga"},
            },
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]
        await _activate(pid)
        item = next(i for i in (await _readiness(client, pid))["items"] if i["id"] == "object-store")
        assert item["state"] == "done"
        store.fail_next("object store unreachable (https://s3.example): refused")
        readiness.forget_object_store_probe()
        item = next(i for i in (await _readiness(client, pid))["items"] if i["id"] == "object-store")
        assert item["state"] == "todo" and "unreachable" in item["detail"]
    finally:
        use_object_store(None)
        readiness.forget_object_store_probe()


async def _store_an_older_copy(project_id: str, name: str, drop: str) -> None:
    """The copy a project made of its template before the template was fixed (#817): the line is gone."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        row = (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == project_id, WorkflowDef.name == name, WorkflowDef.is_active
                )
            )
        ).scalar_one()
        assert drop in row.yaml
        row.yaml = row.yaml.replace(drop, "")


def _template_item(body: dict[str, Any]) -> dict[str, Any] | None:
    return next((i for i in body["items"] if i["id"] == "workflow-template"), None)


async def test_a_workflow_copied_before_its_template_was_fixed_is_flagged_until_republished(
    client: AsyncClient, admin: str
) -> None:
    from pathlib import Path

    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "research",
            "name": "Research",
            "template_ref": "investment-research@0.1.0",
            "config": {"slug": "research", "org": "varga"},
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    await _activate(pid)
    fresh = _template_item(await _readiness(client, pid))
    assert fresh is not None and fresh["state"] == "done", fresh
    await _store_an_older_copy(
        pid,
        "campaign",
        "    # stored as that document, not as markdown the job cannot find (S38-13, RESEARCH-2 on dev).\n",
    )
    commented = _template_item(await _readiness(client, pid))
    assert commented is not None and commented["state"] == "done", "a comment is not the definition"

    await _store_an_older_copy(pid, "campaign", "    output_types: { strategies: json }\n")
    item = _template_item(await _readiness(client, pid))
    assert item is not None and item["state"] == "check", item
    assert "`campaign` v1" in item["detail"] and "`./workflows/campaign.yaml`" in item["detail"]
    assert "`product-memo`" not in item["detail"], "the workflow that did not change is not named"

    shipped = Path(__file__).resolve().parents[3] / "templates" / "investment-research" / "workflows"
    put = await client.put(
        f"/api/v1/projects/{pid}/workflows/campaign",
        json={"yaml": (shipped / "campaign.yaml").read_text(), "source": "template", "activate": True},
    )
    assert put.status_code == 200, put.text
    republished = _template_item(await _readiness(client, pid))
    assert republished is not None and republished["state"] == "done", republished


async def test_a_workflow_published_by_hand_or_a_project_with_no_template_is_not_judged(
    client: AsyncClient, admin: str
) -> None:
    from pathlib import Path

    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "research",
            "name": "Research",
            "template_ref": "investment-research@0.1.0",
            "config": {"slug": "research", "org": "varga"},
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    await _activate(pid)
    shipped = Path(__file__).resolve().parents[3] / "templates" / "investment-research" / "workflows"
    mine = (shipped / "campaign.yaml").read_text().replace("    output_types: { strategies: json }\n", "")
    put = await client.put(
        f"/api/v1/projects/{pid}/workflows/campaign",
        json={"yaml": mine, "source": "platform", "activate": True},
    )
    assert put.status_code == 200, put.text
    item = _template_item(await _readiness(client, pid))
    assert item is not None and item["state"] == "done" and "`campaign`" not in item["detail"], item

    bare = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "bare", "name": "Bare", "config": {"slug": "bare", "org": "varga"}},
    )
    assert bare.status_code == 201, bare.text
    await _activate(bare.json()["id"])
    assert _template_item(await _readiness(client, bare.json()["id"])) is None
