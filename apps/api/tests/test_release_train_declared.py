"""A project has trains only when one of its active workflows declares a `via: release_train`
transition (S30-07): `has_release_train` tells the console whether to show them. The HR template has
none; the software delivery template takes the train to production."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


async def _project(client: AsyncClient, slug: str, template: str) -> dict[str, Any]:
    config: dict[str, Any] = {"slug": slug, "org": "varga"}
    if template.startswith("github-"):
        config["repo"] = {"url": "https://github.com/varga/sandbox.git", "default_branch": "main"}
    created = await client.post(
        f"{ORG}/projects", json={"slug": slug, "name": slug, "template_ref": template, "config": config}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_an_hr_project_has_no_train(client: AsyncClient, admin: str) -> None:
    created = await _project(client, "hr", "joiners-leavers")
    assert created["has_release_train"] is False
    read = (await client.get(f"/api/v1/projects/{created['id']}")).json()
    assert read["has_release_train"] is False
    listed = {p["slug"]: p for p in (await client.get(f"{ORG}/projects")).json()["items"]}
    assert listed["hr"]["has_release_train"] is False


async def test_a_software_delivery_project_takes_the_train(client: AsyncClient, admin: str) -> None:
    created = await _project(client, "dev", "github-software-delivery@1.0.0")
    read = (await client.get(f"/api/v1/projects/{created['id']}")).json()
    assert read["has_release_train"] is True
    listed = {p["slug"]: p for p in (await client.get(f"{ORG}/projects")).json()["items"]}
    assert listed["dev"]["has_release_train"] is True


async def test_only_active_workflows_count(client: AsyncClient, admin: str) -> None:
    """A train-taking workflow published then deactivated no longer gives the project a train."""
    created = await _project(client, "hr-2", "joiners-leavers")
    pid = created["id"]
    yaml = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: shipping, version: 1 }
initial: open
actors:
  dev: { type: human, group: dev }
states:
  open: { display: Open }
  merged: { display: Merged }
  prod: { display: In production, terminal: true, production: true }
transitions:
  - { id: t-merge, from: open, to: merged, by: dev }
  - { id: t-ship, from: merged, to: prod, via: release_train, train: { env: prod } }
"""
    published = await client.put(f"/api/v1/projects/{pid}/workflows/shipping", json={"yaml": yaml})
    assert published.status_code in (200, 201), published.text
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["has_release_train"] is True
    deactivated = await client.post(f"/api/v1/projects/{pid}/workflows/shipping/deactivate")
    assert deactivated.status_code in (200, 204), deactivated.text
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["has_release_train"] is False
