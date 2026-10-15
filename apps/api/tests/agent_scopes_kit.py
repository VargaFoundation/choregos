"""What the agent-scope tests share (S39-09, ADR 0061): people with roles, projects, agents."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
SPEC: dict[str, Any] = {"instructions": "Review the change and say what you found."}
ADMIN = "admin@varga.dev"


async def as_user(client: AsyncClient, email: str) -> None:
    await login(client, email)


async def make_project(client: AsyncClient, slug: str) -> dict[str, Any]:
    """As the organisation's administrator."""
    await as_user(client, ADMIN)
    response = await client.post(
        f"{ORG}/projects",
        json={
            "slug": slug,
            "name": slug,
            "config": {
                "slug": slug,
                "org": "varga",
                "repo": {
                    "url": f"https://github.com/varga/{slug}.git",
                    "default_branch": "main",
                    "language": "python",
                    "test_command": "make test",
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def add_member(client: AsyncClient, email: str, role: str, project: str | None = None) -> None:
    """As the organisation's administrator: an organisation role, or a role in one project."""
    await as_user(client, ADMIN)
    body: dict[str, Any] = {"email": email, "role": role}
    if project:
        body["project_slug"] = project
    response = await client.post(f"{ORG}/members", json=body)
    assert response.status_code in {200, 201}, response.text


async def create_agent(client: AsyncClient, slug: str, **extra: Any) -> Any:
    return await client.post(
        f"{ORG}/agents", json={"slug": slug, "display_name": slug, "spec": SPEC, **extra}
    )


async def install_common(client: AsyncClient, slug: str = "developer") -> dict[str, Any]:
    """A common (platform) agent, installed from the catalogue by the administrator."""
    await as_user(client, ADMIN)
    response = await client.post(f"{ORG}/agent-catalogue/{slug}/install")
    assert response.status_code == 201, response.text
    return dict(response.json())
