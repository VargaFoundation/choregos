"""La matrice RBAC, générée : cinq rôles × les routes qui portent une permission.

Un seul test couvrait 18 permissions × 5 rôles × ~60 routes (état des lieux du
2026-09-24). Ici l'attendu n'est pas écrit à la main : il se DÉDUIT de `ROLE_PERMISSIONS`
et de la permission que chaque route déclare. Une route qui changerait de permission, ou
un rôle qui en gagnerait une, se voit ici — dans les deux sens.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import Role
from httpx import ASGITransport, AsyncClient

from .conftest import login

ROLES = [Role.VIEWER, Role.DEVELOPER, Role.RELEASE_CAPTAIN, Role.PROJECT_OWNER, Role.ORG_ADMIN]


def _routes(project_id: str, workflow_yaml: str, policy_yaml: str) -> list[tuple[str, str, str, Any]]:
    """(permission attendue, méthode, chemin, corps VALIDE).

    Un corps invalide ferait un 422 avant le RBAC : chaque corps est valide.
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
        ("audit:read", "GET", "/api/v1/audit", None),
        ("platform:admin", "PUT", "/api/v1/platform/backends", {"name": "claude-code", "enabled": True}),
        ("project:delete", "DELETE", f"/api/v1/projects/{project_id}", None),
    ]


async def _membre(email: str, role: Role) -> None:
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
async def test_chaque_role_obtient_exactement_ses_permissions(
    client: AsyncClient, project: dict[str, Any], role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.rbac import ROLE_PERMISSIONS, Permission

    # le dev-login ne doit pas rétrograder ni promouvoir : on pose le rôle en base, puis on
    # se connecte comme ce membre (aucun admin de dev, donc `developers` par défaut ignoré)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    reset_settings_cache()
    workflow_yaml = (await client.get(f"/api/v1/projects/{project['id']}/workflow")).json()["yaml"]
    policy_yaml = (await client.get(f"/api/v1/projects/{project['id']}/policy")).json()["yaml"]
    # tracker interne : sinon `POST /work-items` répond 409 pour tout le monde, avant ou après le RBAC
    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )

    email = f"{role}@varga.dev"
    await _membre(email, role)
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, email)
        me = (await c.get("/api/v1/me")).json()
        assert {m["role"] for m in me["memberships"]} == {str(role)}, "le dev-login a touché au rôle"
        ecarts: list[str] = []
        for permission, methode, chemin, corps in _routes(project["id"], workflow_yaml, policy_yaml):
            attendu = Permission(permission) in ROLE_PERMISSIONS[role]
            reponse = await c.request(methode, chemin, json=corps)
            refuse = reponse.status_code == 403
            if refuse == attendu:
                ecarts.append(
                    f"{methode} {chemin} → {reponse.status_code} (attendu {'autorisé' if attendu else '403'})"
                )
            assert reponse.status_code != 422, (
                f"corps invalide pour {methode} {chemin} : {reponse.text[:200]}"
            )
        assert not ecarts, "\n".join(ecarts)
    reset_settings_cache()
