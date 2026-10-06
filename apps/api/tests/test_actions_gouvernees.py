"""Les actions gouvernées dans le cœur (ADR 0035, S20-01) : proposées, décidées — jamais exécutées
dans la requête qui les approuve. Une approbation suffisante démarre `action-<id>` dans Temporal.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

EFFETS = [
    {"effect": "connector.call", "with": {"connector": "entra-acme", "operation": "create_user",
                                          "arguments": {"upn": "{{ params.upn }}", "display_name": "Léa"}}},
]  # fmt: skip


@pytest.fixture(autouse=True)
async def annuaire(client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """L'annuaire que ces actions appellent, déclaré dans l'organisation : une action qui appelle
    une opération inconnue — ou interdite à ce projet — est refusée dès sa proposition (S20-05)."""
    import choregos_adapters
    from choregos_adapters.fakes.entra import FakeEntra

    monkeypatch.setattr(choregos_adapters, "FAUX_ENTRA", FakeEntra())
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret-du-connecteur")
    corps = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
             "secret_refs": {"client_secret": "env:ENTRA_CLIENT_SECRET"}}  # fmt: skip
    cree = await client.post("/api/v1/orgs/varga/connectors", json=corps)
    assert cree.status_code == 201, cree.text


async def _membre(client: AsyncClient, email: str, role: str) -> None:
    reponse = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert reponse.status_code in {200, 201}, reponse.text


async def _proposer(client: AsyncClient, pid: str, **extra: Any) -> dict[str, Any]:
    corps = {
        "kind": "entra.create_user",
        "title": "Créer le compte de Léa",
        "params": {"upn": "lea@acme.test"},
        "effects": EFFETS,
        **extra,
    }
    reponse = await client.post(f"/api/v1/projects/{pid}/actions", json=corps)
    assert reponse.status_code == 201, reponse.text
    return dict(reponse.json())


async def _session(app: Any, email: str) -> AsyncClient:
    autre = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(autre, email)
    return autre


async def test_une_action_approuvee_part_dans_temporal_sans_aucun_effet_dans_la_requete(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    pid = project["id"]
    await _membre(client, "dev@varga.dev", "developer")
    dev = await _session(app, "dev@varga.dev")
    try:
        action = await _proposer(dev, pid)
    finally:
        await dev.aclose()
    assert action["status"] == "pending_approval"
    assert action["proposed_by"] == {"kind": "user", "id": "dev@varga.dev", "via": "api"}

    decidee = await client.post(
        f"/api/v1/projects/{pid}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert decidee.status_code == 200, decidee.text
    corps = decidee.json()
    assert corps["status"] == "approved"
    assert corps["temporal_wf_id"] == f"action-{action['id']}"
    assert corps["journal"] == [], "aucun effet dans la requête : la réponse part avant"
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    assert fake.started[f"action-{action['id']}"] == {"action_id": action["id"]}
    assert corps["decisions"][0]["by"] == "admin@varga.dev"
    assert corps["decisions"][0]["auth_age_seconds"] is not None


async def test_qui_propose_ne_decide_pas(client: AsyncClient, project: dict[str, Any]) -> None:
    action = await _proposer(client, project["id"])
    refus = await client.post(
        f"/api/v1/projects/{project['id']}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert refus.status_code == 422 and "séparation" in refus.text


async def test_un_rejet_dit_pourquoi_et_une_action_decidee_ne_se_redecide_pas(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    pid = project["id"]
    await _membre(client, "dev@varga.dev", "developer")
    dev = await _session(app, "dev@varga.dev")
    try:
        action = await _proposer(dev, pid)
    finally:
        await dev.aclose()
    base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
    assert (await client.post(base, json={"decision": "reject"})).status_code == 422
    rejetee = await client.post(base, json={"decision": "reject", "reason": "pas encore embauchée"})
    assert rejetee.status_code == 200 and rejetee.json()["status"] == "rejected"
    assert (await client.post(base, json={"decision": "approve"})).status_code == 409


async def test_un_developpeur_ne_decide_pas_et_un_jeton_d_api_non_plus(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    pid = project["id"]
    action = await _proposer(client, pid)
    await _membre(client, "dev@varga.dev", "developer")
    jeton = (await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["*"]})).json()["token"]
    dev = await _session(app, "dev@varga.dev")
    try:
        base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
        assert (await dev.post(base, json={"decision": "approve"})).status_code == 403
    finally:
        await dev.aclose()
    par_jeton = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        refus = await par_jeton.post(
            base, json={"decision": "approve"}, headers={"Authorization": f"Bearer {jeton}"}
        )
        assert refus.status_code == 403 and "session humaine" in refus.text
    finally:
        await par_jeton.aclose()


async def test_une_session_trop_ancienne_doit_se_reauthentifier(
    client: AsyncClient, project: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from choregos_api import fraicheur

    pid = project["id"]
    await _membre(client, "dev@varga.dev", "developer")
    dev = await _session(app, "dev@varga.dev")
    try:
        action = await _proposer(dev, pid)
    finally:
        await dev.aclose()
    plus_tard = time.time() + 11 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: plus_tard)
    refus = await client.post(
        f"/api/v1/projects/{pid}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert refus.status_code == 401
    assert refus.json()["errors"][0]["error"] == "step_up_required"
    assert (await client.get(f"/api/v1/projects/{pid}/actions/{action['id']}")).json()[
        "status"
    ] == "pending_approval"


async def test_il_en_faut_deux_quand_la_regle_en_demande_deux(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    pid = project["id"]
    await _membre(client, "po@varga.dev", "project_owner")
    await _membre(client, "dev@varga.dev", "developer")
    dev = await _session(app, "dev@varga.dev")
    try:
        action = await _proposer(dev, pid, approval={"approvers": [{"role": "project_owner", "min": 2}]})
    finally:
        await dev.aclose()
    base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
    premiere = await client.post(base, json={"decision": "approve"})
    assert premiere.status_code == 200 and premiere.json()["status"] == "pending_approval"
    assert f"action-{action['id']}" not in get_temporal().started  # type: ignore[attr-defined]
    assert (await client.post(base, json={"decision": "approve"})).status_code == 409, "pas deux fois la même"
    po = await _session(app, "po@varga.dev")
    try:
        seconde = await po.post(base, json={"decision": "approve"})
    finally:
        await po.aclose()
    assert seconde.status_code == 200 and seconde.json()["status"] == "approved"
    assert f"action-{action['id']}" in get_temporal().started  # type: ignore[attr-defined]


async def test_de_deux_approbations_simultanees_une_seule_demarre_l_action(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    pid = project["id"]
    await _membre(client, "po@varga.dev", "project_owner")
    await _membre(client, "dev@varga.dev", "developer")
    dev = await _session(app, "dev@varga.dev")
    try:
        action = await _proposer(dev, pid)
    finally:
        await dev.aclose()
    po = await _session(app, "po@varga.dev")
    try:
        base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
        reponses = await asyncio.gather(
            client.post(base, json={"decision": "approve"}), po.post(base, json={"decision": "approve"})
        )
    finally:
        await po.aclose()
    assert sorted(r.status_code for r in reponses) == [200, 409]
    assert list(get_temporal().started).count(f"action-{action['id']}") == 1  # type: ignore[attr-defined]


async def test_un_effet_inconnu_est_refuse_a_la_proposition(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    refus = await client.post(
        f"/api/v1/projects/{project['id']}/actions",
        json={"kind": "x", "title": "x", "effects": [{"effect": "effacer.tout"}]},
    )
    assert refus.status_code == 422 and "effacer.tout" in refus.text


async def test_une_preuve_se_remet_a_l_action_qui_l_attend() -> None:
    """La couture d'une preuve (S20-08) : ce qui la constate la remet à `action-<id>`, par signal."""
    from choregos_api.services.actions import signaler_une_preuve
    from choregos_api.temporal import FakeTemporal, set_temporal

    fake = FakeTemporal()
    set_temporal(fake)
    try:
        await signaler_une_preuve("a1", 2, False, "la clé est toujours là")
    finally:
        set_temporal(None)
    attendu = {"position": 2, "ok": False, "detail": "la clé est toujours là"}
    assert fake.signals == [("action-a1", "preuve", attendu)]


async def test_une_regle_sans_step_up_n_exige_pas_d_authentification_recente(
    client: AsyncClient, project: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une politique de greffon peut ne pas exiger d'authentification récente (S20-08) : `step_up_minutes`
    absent. Une action proposée par l'API en a toujours une ; celle-ci se décide sans 401."""
    import time

    from choregos_api import fraicheur
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    pid = project["id"]
    await _membre(client, "dev@varga.dev", "developer")
    dev = await _session(app, "dev@varga.dev")
    try:
        action = await _proposer(dev, pid)
    finally:
        await dev.aclose()
    async with session_scope(orgs="*") as session:
        ligne = await session.get(Action, action["id"])
        assert ligne is not None
        ligne.approval = {**ligne.approval, "step_up_minutes": None}
    plus_tard = time.time() + 11 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: plus_tard)
    decidee = await client.post(
        f"/api/v1/projects/{pid}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert decidee.status_code == 200, decidee.text
    assert decidee.json()["decisions"][0]["auth_age_seconds"] >= 11 * 60, "l'âge reste consigné"
