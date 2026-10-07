"""Retirer un membre (S17-02) : l'administration ne savait qu'inviter ou changer un rôle.

Le dernier administrateur d'une organisation ne se retire pas : elle ne se gérerait plus que par
la base.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login


async def _membre(client: AsyncClient, email: str, role: str, projet: str | None = None) -> dict[str, Any]:
    reponse = await client.post(
        "/api/v1/orgs/varga/members", json={"email": email, "role": role, "project_slug": projet}
    )
    assert reponse.status_code in {200, 201}, reponse.text
    return dict(reponse.json())


async def _membres(client: AsyncClient) -> set[tuple[str | None, str | None, str]]:
    lignes = (await client.get("/api/v1/orgs/varga/members")).json()
    return {(m["email"], m.get("project_slug"), m["role"]) for m in lignes}


async def test_un_membre_se_retire_et_l_audit_le_dit(client: AsyncClient, admin: str) -> None:
    dev = await _membre(client, "dev@varga.dev", "developer")
    retire = await client.delete(f"/api/v1/orgs/varga/members/{dev['user_id']}")
    assert retire.status_code == 204, retire.text
    assert ("dev@varga.dev", None, "developer") not in await _membres(client)
    audit = (await client.get("/api/v1/audit")).json()["items"]
    assert any(a["action"] == "member.remove" and a["target_id"] == dev["user_id"] for a in audit)


async def test_le_dernier_administrateur_ne_se_retire_pas(client: AsyncClient, admin: str) -> None:
    moi = next(m for m in (await client.get("/api/v1/orgs/varga/members")).json() if m["email"] == admin)
    seul = await client.delete(f"/api/v1/orgs/varga/members/{moi['user_id']}")
    assert seul.status_code == 409 and "last administrator" in seul.text
    await _membre(client, "second@varga.dev", "org_admin")
    assert (await client.delete(f"/api/v1/orgs/varga/members/{moi['user_id']}")).status_code == 204


async def test_retirer_d_un_projet_laisse_l_organisation(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    dev = await _membre(client, "dev@varga.dev", "developer")
    await _membre(client, "dev@varga.dev", "project_owner", projet="billing-api")
    retire = await client.delete(
        f"/api/v1/orgs/varga/members/{dev['user_id']}", params={"project": "billing-api"}
    )
    assert retire.status_code == 204, retire.text
    restants = await _membres(client)
    assert ("dev@varga.dev", None, "developer") in restants
    assert ("dev@varga.dev", "billing-api", "project_owner") not in restants


async def test_un_developpeur_ne_retire_personne(client: AsyncClient, admin: str) -> None:
    autre = await _membre(client, "autre@varga.dev", "viewer")
    await _membre(client, "dev@varga.dev", "developer")
    await login(client, "dev@varga.dev")
    assert (await client.delete(f"/api/v1/orgs/varga/members/{autre['user_id']}")).status_code == 403
