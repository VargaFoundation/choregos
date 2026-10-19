"""Des connecteurs par capacités (ADR 0034, S19-01).

Les types viennent du registre ; un projet voit ce que ses workflows exigent, et pourquoi ; un
secret ne s'écrit qu'en référence, et la référence est RÉSOLUE quand le connecteur sert.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

ARRIVEE = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: arrivee, version: 1 }
actors:
  coordinateur: { type: agent, role: plan, model: "profile:standard" }
  rh: { type: human, group: rh, sla_hours: 24 }
states:
  demande: { display: Demande, kind: wait }
  plan: { display: Plan d'accès }
  valide: { display: Validé, terminal: true }
transitions:
  - { id: t-plan, from: demande, to: plan, by: coordinateur, outputs: [plan], gates: [outputs_present] }
  - { id: t-valider, from: plan, to: valide, by: rh }
"""


async def test_les_types_viennent_du_registre(client: AsyncClient, admin: str) -> None:
    types = {(t["kind"], t["type"]): t for t in (await client.get("/api/v1/connectors/types")).json()}
    jira = types[("tracker", "jira")]
    assert jira["available"] is True, "enregistré, donc disponible"
    assert jira["capabilities"] == ["tracker"]
    assert {"api_token", "webhook_secret"} <= set(jira["secret_fields"])
    assert "api_token" not in jira["config_schema"]["properties"]
    assert not [t for (_, t) in types if t == "fake"]


async def test_un_projet_rh_n_affiche_ni_depot_ni_ci_ni_train(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    logiciel = [r["capability"] for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()]
    assert {"scm", "cd"} <= set(logiciel), "le gabarit par défaut livre du logiciel"
    assert "ci" not in logiciel, "les checks de la PR se lisent par le dépôt (S21-23)"

    assert (
        await client.put(f"/api/v1/projects/{pid}/workflows/arrivee", json={"yaml": ARRIVEE})
    ).status_code in {200, 201}
    routage = {"default": "arrivee", "rules": []}
    assert (await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routage)).status_code == 200
    assert (
        await client.post(f"/api/v1/projects/{pid}/workflows/default-simple/deactivate")
    ).status_code == 204

    exigences = (await client.get(f"/api/v1/projects/{pid}/requirements")).json()
    assert [r["capability"] for r in exigences] == ["tracker", "runtime", "gateway"]
    assert any("arrivee: the agent coordinateur" in raison for raison in exigences[1]["reasons"])
    assert exigences[0]["connector"] is None
    assert exigences[0]["default_type"] is None, "a tracker is not provided by the deployment (S40-05)"
    assert exigences[1]["default_type"], "the runtime is"


async def test_only_what_the_deployment_provides_is_a_platform_default(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """S40-05: `cd` said "Platform default: argocd" and `ci` "tekton" with nothing behind them."""
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    pid = project["id"]
    items = {r["capability"]: r for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()}
    assert {"scm", "cd", "runtime"} <= set(items)
    assert items["cd"]["default_type"] is None and items["scm"]["default_type"] is None
    assert items["runtime"]["default_type"] == "k8s_job"


async def test_un_secret_en_clair_recoit_422(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    corps = {"type": "jira", "config": {"base_url": "https://acme.atlassian.net", "email": "a@b.c",
                                         "project_key": "RH", "api_token": "s3cr3t"}}  # fmt: skip
    refus = await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=corps)
    assert refus.status_code == 422 and "in clear" in refus.text
    assert "s3cr3t" not in refus.text

    corps["config"].pop("api_token")
    for references, motif in (
        ({"api_token": "s3cr3t"}, "reference"),
        ({"api_token": "coffre:x"}, "unknown"),
        ({"mot_de_passe": "env:X"}, "secret field"),
    ):
        refus = await client.put(
            f"/api/v1/projects/{pid}/connectors/tracker", json={**corps, "secret_refs": references}
        )
        assert refus.status_code == 422 and motif in refus.text, (references, refus.text)
    inconnu = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "trello", "config": {}}
    )
    assert inconnu.status_code == 422


async def test_une_reference_est_resolue_quand_le_connecteur_sert(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = project["id"]
    corps = {
        "type": "jira",
        "config": {"base_url": "https://acme.atlassian.net", "email": "a@b.c", "project_key": "RH"},
        "secret_refs": {"api_token": "env:CONNECTOR_TEST_JIRA_TOKEN"},
    }
    pose = await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=corps)
    assert pose.status_code == 200, pose.text
    assert pose.json()["secret_refs"] == {"api_token": "env:CONNECTOR_TEST_JIRA_TOKEN"}

    monkeypatch.delenv("CONNECTOR_TEST_JIRA_TOKEN", raising=False)
    absent = (await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")).json()
    assert absent["ok"] is False
    assert "CONNECTOR_TEST_JIRA_TOKEN" in str(absent["checks"])

    monkeypatch.setenv("CONNECTOR_TEST_JIRA_TOKEN", "s3cr3t")
    present = (await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")).json()
    assert present["ok"] is True, present
    assert "s3cr3t" not in str(present)
    couvert = {r["capability"]: r for r in (await client.get(f"/api/v1/projects/{pid}/requirements")).json()}
    assert couvert["tracker"]["connector"]["type"] == "jira"
    assert couvert["tracker"]["default_type"] is None


async def test_l_adaptateur_teste_recoit_le_secret_resolu_et_la_reponse_ne_le_dit_pas(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hors des faux : le test du connecteur construit l'adaptateur avec la valeur, jamais avec
    la référence — et rien de la valeur ne remonte dans la réponse."""
    from choregos_adapters import ConnectorTypeSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    recus: list[dict[str, Any]] = []
    spec = ConnectorTypeSpec("sonde", ("tracker",), {"type": "object", "properties": {}}, ("api_token",))
    register("tracker", "sonde", spec)(lambda cfg: recus.append(cfg) or object())
    try:
        pid = project["id"]
        corps = {"type": "sonde", "config": {}, "secret_refs": {"api_token": "env:CONNECTOR_TEST_SONDE"}}
        assert (await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=corps)).status_code == 200
        monkeypatch.setenv("CONNECTOR_TEST_SONDE", "v4leur")
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        resultat = await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")
    finally:
        _REGISTRY.pop(("tracker", "sonde"), None)
        _SPECS.pop(("tracker", "sonde"), None)
    assert recus == [{"api_token": "v4leur"}]
    assert "v4leur" not in resultat.text


# ── S39-06b: a reference reads connector variables only, and only an administrator writes one ──


def _jira(host: str, reference: str) -> dict[str, Any]:
    return {
        "type": "jira",
        "config": {"base_url": f"https://{host}", "email": "a@b.c", "project_key": "RH"},
        "secret_refs": {"api_token": reference},
    }


async def test_a_project_owner_cannot_write_an_env_reference(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    from .conftest import login

    url = f"/api/v1/projects/{project['id']}/connectors/tracker"
    # The organisation's administrator (the fixture's session) may.
    assert (await client.put(url, json=_jira("acme.atlassian.net", "env:CONNECTOR_X"))).status_code == 200
    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = User(email="proprio@varga.dev", display_name="proprio")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role="project_owner"))
    await login(client, "proprio@varga.dev")
    refused = await client.put(url, json=_jira("acme.atlassian.net", "env:CONNECTOR_X"))
    assert refused.status_code == 422, refused.text
    assert "administrator" in refused.json()["detail"]


async def test_a_platform_secret_never_leaves_through_a_connector_test(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    import respx
    from choregos_api.db.models import Connector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    monkeypatch.setenv("CHOREGOS_SESSION_SECRET", "platform-session-secret")
    url = f"/api/v1/projects/{project['id']}/connectors/tracker"
    with respx.mock(assert_all_called=False) as mock:
        host = mock.route(host="evil.test").mock(return_value=httpx.Response(200, json={}))
        # Refused at write, for the administrator too.
        refused = await client.put(url, json=_jira("evil.test", "env:CHOREGOS_SESSION_SECRET"))
        assert refused.status_code == 422, refused.text
        # A reference stored before the allowlist is never resolved: the connector is in error.
        await client.put(url, json=_jira("evil.test", "env:CONNECTOR_X"))
        async with session_scope() as session:
            row = (
                await session.execute(select(Connector).where(Connector.project_id == project["id"]))
            ).scalar_one()
            row.secret_refs = {"api_token": "env:CHOREGOS_SESSION_SECRET"}
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        tested = await client.post(f"{url}/test")
        assert "platform-session-secret" not in tested.text
        assert tested.json()["ok"] is False
        assert not host.called, "nothing was sent to the connector's host"
