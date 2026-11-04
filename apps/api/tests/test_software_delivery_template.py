"""The `github-software-delivery` template (S21-23): three workflows, their routing, its policy, and the
catalogue agents they name, installed in the organisation when the project is born."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

AGENTS = {
    "triager",
    "spec-writer",
    "planner",
    "developer",
    "tester",
    "reviewer",
    "security-reviewer",
    "review-responder",
    "ci-fixer",
    "docs-writer",
    "release-notes-writer",
    "prod-verifier",
    "researcher",
    "architect",
}


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


async def test_a_project_born_from_the_template_receives_its_three_workflows_its_policy_and_its_agents(
    client: AsyncClient, admin: str
) -> None:
    pid = (await _project(client))["id"]
    active = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert active == {"dev-simple", "study", "dev-complex"}
    assert (await client.get(f"/api/v1/projects/{pid}/policy")).json()["name"] == "software-delivery"

    agents = {a["slug"] for a in (await client.get("/api/v1/orgs/varga/agents")).json()}
    assert agents >= AGENTS, sorted(AGENTS - agents)
    skills = {s["slug"] for s in (await client.get("/api/v1/orgs/varga/skills")).json()}
    assert "madr-4" in skills

    # The PR's checks are read from the repository: nothing asks a GitHub Actions project for a Tekton.
    requirements = [
        r["capability"] for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()
    ]
    assert {"tracker", "scm", "cd", "runtime", "gateway"} <= set(requirements)
    assert "ci" not in requirements


async def test_the_labels_route_to_the_right_workflow(client: AsyncClient, admin: str) -> None:
    pid = (await _project(client))["id"]
    assert (
        await client.put(
            f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
        )
    ).status_code == 200
    for labels, expected in (
        ([], "dev-simple"),
        (["bug"], "dev-simple"),
        (["adr"], "study"),
        (["spike", "bug"], "study"),
        (["bug", "complex"], "dev-complex"),
        (["feature"], "dev-complex"),
    ):
        item = await client.post(
            f"/api/v1/projects/{pid}/work-items", json={"title": "x", "labels": labels, "start": False}
        )
        assert item.status_code == 201, item.text
        assert (item.json()["workflow_name"], item.json()["state"]) == (expected, "inbox"), labels


def test_each_workflow_takes_the_train_it_announces_after_the_merge() -> None:
    """What the GitHub webhook boards (ADR 0041): prod without approval for a fix, staging first
    for a feature, nothing for a study."""
    from pathlib import Path

    from choregos_core import parse_workflow
    from choregos_core.dsl.trains import train_after_merge, train_approval, train_env

    folder = Path(__file__).resolve().parents[3] / "templates" / "github-software-delivery" / "workflows"
    expected = {
        "dev-simple": ("prod", {"required": False, "group": None}),
        "dev-complex": ("staging", {"required": False, "group": None}),
        "study": None,
    }
    for name, wanted in expected.items():
        workflow = parse_workflow((folder / f"{name}.yaml").read_text(encoding="utf-8"))[0]
        train = train_after_merge(workflow)
        found = None if train is None else (train_env(train), dict(train_approval(workflow, train)))
        assert found == wanted, name
