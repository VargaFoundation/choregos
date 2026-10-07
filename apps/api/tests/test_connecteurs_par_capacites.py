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
    assert {"scm", "ci", "cd"} <= set(logiciel), "le gabarit par défaut livre du logiciel"

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
    assert exigences[0]["connector"] is None and exigences[0]["default_type"]


async def test_un_secret_en_clair_recoit_422(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    corps = {"type": "jira", "config": {"base_url": "https://acme.atlassian.net", "email": "a@b.c",
                                         "project_key": "RH", "api_token": "s3cr3t"}}  # fmt: skip
    refus = await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=corps)
    assert refus.status_code == 422 and "en clair" in refus.text
    assert "s3cr3t" not in refus.text

    corps["config"].pop("api_token")
    for references, motif in (
        ({"api_token": "s3cr3t"}, "reference"),
        ({"api_token": "coffre:x"}, "unknown"),
        ({"mot_de_passe": "env:X"}, "champ secret"),
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
        "secret_refs": {"api_token": "env:CHOREGOS_TEST_JIRA_TOKEN"},
    }
    pose = await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=corps)
    assert pose.status_code == 200, pose.text
    assert pose.json()["secret_refs"] == {"api_token": "env:CHOREGOS_TEST_JIRA_TOKEN"}

    monkeypatch.delenv("CHOREGOS_TEST_JIRA_TOKEN", raising=False)
    absent = (await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")).json()
    assert absent["ok"] is False
    assert "CHOREGOS_TEST_JIRA_TOKEN" in str(absent["checks"])

    monkeypatch.setenv("CHOREGOS_TEST_JIRA_TOKEN", "s3cr3t")
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
        corps = {"type": "sonde", "config": {}, "secret_refs": {"api_token": "env:CHOREGOS_TEST_SONDE"}}
        assert (await client.put(f"/api/v1/projects/{pid}/connectors/tracker", json=corps)).status_code == 200
        monkeypatch.setenv("CHOREGOS_TEST_SONDE", "v4leur")
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        resultat = await client.post(f"/api/v1/projects/{pid}/connectors/tracker/test")
    finally:
        _REGISTRY.pop(("tracker", "sonde"), None)
        _SPECS.pop(("tracker", "sonde"), None)
    assert recus == [{"api_token": "v4leur"}]
    assert "v4leur" not in resultat.text
