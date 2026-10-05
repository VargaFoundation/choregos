"""Le registre d'agents de l'organisation (ADR 0033, S18-01).

Un agent est un objet de l'organisation ; ce qu'il EST vit dans ses versions, qui ne se modifient
jamais. Un projet épingle une version et ne peut que la resserrer.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login

SPEC: dict[str, Any] = {
    "instructions": "Prépare le plan d'accès d'une arrivée : comptes, groupes, matériel.",
    "model": "profile:standard",
    "limits": {"max_turns": 40, "max_minutes": 30},
    "budget": {"run_usd": 2.0, "daily_usd": 20.0},
    "skills": [{"slug": "procedure-onboarding"}],
    "mcp_servers": [{"connector": "entra", "tools": ["entra_lire_*", "entra_creer_compte"]}],
}


async def _creer(client: AsyncClient, slug: str = "coordinateur-onboarding", **extra: Any) -> Any:
    return await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": slug, "display_name": "Coordinateur onboarding", "spec": SPEC, **extra},
    )


async def test_un_agent_nait_avec_sa_version_1(client: AsyncClient, admin: str) -> None:
    cree = await _creer(client)
    assert cree.status_code == 201, cree.text
    agent = cree.json()
    assert (agent["kind"], agent["status"], agent["latest_version"], agent["owner"]) == (
        "internal",
        "active",
        1,
        admin,
    )
    assert agent["versions"][0]["spec"]["budget"] == {"run_usd": 2.0, "daily_usd": 20.0}
    assert [a["slug"] for a in (await client.get("/api/v1/orgs/varga/agents")).json()] == [
        "coordinateur-onboarding"
    ]
    assert (await _creer(client)).status_code == 409, "un slug par organisation"


async def test_publier_ne_reecrit_jamais_une_version(client: AsyncClient, admin: str) -> None:
    await _creer(client)
    v1 = (await client.get("/api/v1/orgs/varga/agents/coordinateur-onboarding/versions/1")).json()
    plus_strict = {**SPEC, "budget": {"run_usd": 1.0, "daily_usd": 10.0}}
    v2 = await client.post("/api/v1/orgs/varga/agents/coordinateur-onboarding/versions", json=plus_strict)
    assert v2.status_code == 201 and v2.json()["version"] == 2, v2.text
    assert v2.json()["checksum"] != v1["checksum"]
    assert (await client.get("/api/v1/orgs/varga/agents/coordinateur-onboarding/versions/1")).json() == v1
    agent = (await client.get("/api/v1/orgs/varga/agents/coordinateur-onboarding")).json()
    assert [v["version"] for v in agent["versions"]] == [2, 1] and agent["latest_version"] == 2


async def test_la_revocation_est_definitive(client: AsyncClient, admin: str) -> None:
    await _creer(client)
    revoque = await client.patch(
        "/api/v1/orgs/varga/agents/coordinateur-onboarding", json={"status": "revoked"}
    )
    assert revoque.status_code == 200 and revoque.json()["revoked_at"], revoque.text
    publier = await client.post("/api/v1/orgs/varga/agents/coordinateur-onboarding/versions", json=SPEC)
    assert publier.status_code == 409
    reactiver = await client.patch(
        "/api/v1/orgs/varga/agents/coordinateur-onboarding", json={"status": "active"}
    )
    assert reactiver.status_code == 409


async def test_un_developpeur_lit_le_registre_mais_n_y_ecrit_pas(client: AsyncClient, admin: str) -> None:
    await _creer(client)
    membre = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert membre.status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert (await client.get("/api/v1/orgs/varga/agents")).status_code == 200
    assert (await _creer(client, slug="autre")).status_code == 403
    assert (
        await client.post("/api/v1/orgs/varga/agents/coordinateur-onboarding/versions", json=SPEC)
    ).status_code == 403


async def test_un_projet_resserre_et_ne_peut_pas_elargir(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _creer(client)
    epingle = f"/api/v1/projects/{project['id']}/agents/coordinateur-onboarding"
    resserre = await client.put(
        epingle,
        json={"version": 1, "overrides": {"budget": {"run_usd": 1.5}, "tools": ["entra_lire_*"]}},
    )
    assert resserre.status_code == 200, resserre.text
    effectif = resserre.json()["effective"]
    assert effectif["budget"] == {"run_usd": 1.5, "daily_usd": 20.0}
    assert effectif["mcp_servers"] == [{"connector": "entra", "tools": ["entra_lire_*"]}]

    for elargit in (
        {"budget": {"daily_usd": 50.0}},
        {"limits": {"max_turns": 100}},
        {"tools": ["entra_supprimer_compte"]},
    ):
        refuse = await client.put(epingle, json={"version": 1, "overrides": elargit})
        assert refuse.status_code == 422, (elargit, refuse.text)
    liste = (await client.get(f"/api/v1/projects/{project['id']}/agents")).json()
    assert [(e["agent"], e["version"]) for e in liste] == [("coordinateur-onboarding", 1)]
    assert (await client.delete(epingle)).status_code == 204
    assert (await client.get(f"/api/v1/projects/{project['id']}/agents")).json() == []


async def test_un_agent_d_une_autre_organisation_ne_s_epingle_pas(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        autre = Organization(slug="autre", name="Autre")
        session.add(autre)
        await session.flush()
        agent = Agent(org_id=autre.id, slug="espion", kind="internal", display_name="Espion", status="active")
        session.add(agent)
        await session.flush()
        session.add(AgentVersion(agent_id=agent.id, org_id=autre.id, version=1, spec={}, checksum="sha256:x"))
    reponse = await client.put(f"/api/v1/projects/{project['id']}/agents/espion", json={"version": 1})
    assert reponse.status_code == 404, reponse.text
    assert (await client.get("/api/v1/orgs/autre/agents")).status_code == 403, "pas membre de `autre`"


async def test_des_instructions_qui_ne_rendent_pas_sont_refusees_a_la_publication(
    client: AsyncClient, admin: str
) -> None:
    """Le bac à sable (ADR 0033) : une évasion, une variable inconnue, une syntaxe cassée."""
    for instructions in ("{{ ''.__class__.__mro__ }}", "{{ inconnue }}", "{% for x in %}"):
        refuse = await _creer(client, spec={**SPEC, "instructions": instructions})
        assert refuse.status_code == 422, (instructions, refuse.text)
    assert (await _creer(client)).status_code == 201
    evasion = {**SPEC, "instructions": "{{ cycler.__init__.__globals__ }}"}
    publier = await client.post("/api/v1/orgs/varga/agents/coordinateur-onboarding/versions", json=evasion)
    assert publier.status_code == 422, publier.text
