"""The RBAC matrix, generated: five roles x the routes that carry a permission.

A single test covered 18 permissions x 5 roles x ~60 routes (state of play of 2026-09-24). Here
the expectation is not written by hand: it is DEDUCED from `ROLE_PERMISSIONS` and from the
permission each route declares. A route that changed permission, or a role that gained one, shows
up here, in both directions.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import Role
from httpx import ASGITransport, AsyncClient

from .conftest import login

ROLES = [Role.VIEWER, Role.DEVELOPER, Role.RELEASE_CAPTAIN, Role.PROJECT_OWNER, Role.ORG_ADMIN]


def _routes(project_id: str, workflow_yaml: str, policy_yaml: str) -> list[tuple[str, str, str, Any]]:
    """(expected permission, method, path, VALID body).

    An invalid body would give a 422 before the RBAC: every body is valid.
    """
    return [
        ("project:read", "GET", f"/api/v1/projects/{project_id}", None),
        ("workflow:write", "PUT", f"/api/v1/projects/{project_id}/workflow", {"yaml": workflow_yaml}),
        ("policy:write", "PUT", f"/api/v1/projects/{project_id}/policy", {"yaml": policy_yaml}),
        (
            "connector:write",
            "PUT",
            f"/api/v1/projects/{project_id}/connectors/notify",
            {"type": "fake", "config": {}},
        ),
        (
            "models:write",
            "PUT",
            f"/api/v1/projects/{project_id}/models",
            {"profiles": {}, "allow_unvalidated": True, "inherited": {}},
        ),
        ("item:control", "POST", f"/api/v1/projects/{project_id}/work-items", {"title": "T", "start": False}),
        ("train:operate", "POST", f"/api/v1/projects/{project_id}/trains/prod/freeze", {"reason": "test"}),
        ("memory:write", "POST", f"/api/v1/projects/{project_id}/memory/reimport", {}),
        ("member:manage", "POST", "/api/v1/orgs/varga/members", {"email": "x@varga.dev", "role": "viewer"}),
        ("member:read", "GET", "/api/v1/orgs/varga/members", None),
        ("audit:read", "GET", "/api/v1/audit", None),
        ("platform:admin", "PUT", "/api/v1/platform/backends", {"name": "claude-code", "enabled": True}),
        ("project:delete", "DELETE", f"/api/v1/projects/{project_id}", None),
        ("agent:manage", "POST", "/api/v1/orgs/varga/agent-catalogue/triager/install", {}),
        # Agent scopes (S39-09, ADR 0061): a personal agent is every working role's, a project's agent
        # its owner's, an organisation agent (`agent:manage`, above) the administrator's.
        (
            "agent:create_personal",
            "POST",
            "/api/v1/orgs/varga/agents",
            {
                "slug": "my-helper",
                "display_name": "Mine",
                "scope": "personal",
                "spec": {"instructions": "Help."},
            },
        ),
        (
            "agent:project_manage",
            "POST",
            "/api/v1/orgs/varga/agents",
            {
                "slug": "project-helper",
                "display_name": "Project's",
                "scope": "project",
                "project": "billing-api",
                "spec": {"instructions": "Help."},
            },
        ),
        # A knowledge base owned by the project (S39-14): its owners create it.
        (
            "project:write",
            "POST",
            f"/api/v1/projects/{project_id}/knowledge",
            {"slug": "kb-matrix", "name": "M"},
        ),
        # Allowlisted by default; the empty fake fetcher answers 502 to whoever may ask.
        (
            "skill:import",
            "POST",
            "/api/v1/orgs/varga/skills/import-git/preview",
            {"repo_url": "https://github.com/anthropics/skills", "ref": "main", "path": "skills"},
        ),
    ]


async def _member(email: str, role: Role) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
            session.add(user)
            await session.flush()
        existing = (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id, Membership.org_id == org.id)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(Membership(user_id=user.id, org_id=org.id, role=str(role)))
        else:
            existing.role = str(role)


@pytest.mark.parametrize("role", ROLES, ids=[str(r) for r in ROLES])
async def test_each_role_gets_exactly_its_permissions(
    client: AsyncClient, project: dict[str, Any], role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.rbac import ROLE_PERMISSIONS, Permission

    # the dev sign-in must neither demote nor promote: the role is set in the database, then we
    # sign in as that member (no dev admin, so the default `developers` is ignored)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    reset_settings_cache()
    workflow_yaml = (await client.get(f"/api/v1/projects/{project['id']}/workflow")).json()["yaml"]
    policy_yaml = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()["yaml"]
    # internal tracker: otherwise `POST /work-items` answers 409 to everyone, before or after the RBAC
    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )

    email = f"{role}@varga.dev"
    await _member(email, role)
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, email)
        me = (await c.get("/api/v1/me")).json()
        assert {m["role"] for m in me["memberships"]} == {str(role)}, "the dev sign-in touched the role"
        gaps: list[str] = []
        for permission, method, path, body in _routes(project["id"], workflow_yaml, policy_yaml):
            expected = Permission(permission) in ROLE_PERMISSIONS[role]
            response = await c.request(method, path, json=body)
            refused = response.status_code == 403
            if refused == expected:
                gaps.append(
                    f"{method} {path} -> {response.status_code} (expected {'allowed' if expected else '403'})"
                )
            assert response.status_code != 422, f"invalid body for {method} {path}: {response.text[:200]}"
        assert not gaps, "\n".join(gaps)
    reset_settings_cache()
