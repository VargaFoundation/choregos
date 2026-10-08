"""Une transition système propose une action gouvernée, à date (S20-05) — côté API.

- Les champs d'un ticket changent par `PATCH /work-items/{id}`, validés par SON workflow, et
  l'interpréteur l'apprend (`fields_changed`) : c'est ce qui réarme une date d'action.
- Un effet inconnu se refuse à la publication du workflow, pas à la date d'arrivée d'un ticket.
- La politique du PROJET compte : une opération qu'il s'interdit ne se propose pas, et une action
  approuvée ne la joue pas.
- Quand chaque opération est permise, la politique décide et l'action naît approuvée ; sinon une
  personne décide, et un rejet prévient le ticket qui l'attend.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
ARRIVEE = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    required: [upn]
    properties:
      upn: {type: string}
      date_arrivee: {type: string, format: date-time}
      poste: {type: string}
initial: prepare
actors:
  plateforme: {type: system}
states:
  prepare: {display: Préparée, kind: wait}
  a_revoir: {display: À revoir, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - id: t-comptes
    from: prepare
    to: fait
    by: plateforme
    action:
      kind: arrivee.comptes
      title: "Les comptes de {{ fields.upn }}"
      params: {upn: "{{ fields.upn }}"}
      effects:
        - effect: EFFET
          with:
            connector: entra-acme
            operation: create_user
            arguments: {upn: "{{ params.upn }}", display_name: Léa}
      not_before: fields.date_arrivee - 10d
    on_fail: {to: prepare, max_attempts: 1, escalate_to: a_revoir}
"""


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.entra import FakeEntra

    faux = FakeEntra()
    monkeypatch.setattr(choregos_adapters, "FAUX_ENTRA", faux)
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret-du-connecteur")
    yield faux


async def _annuaire(client: AsyncClient) -> None:
    corps = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
             "secret_refs": {"client_secret": "env:ENTRA_CLIENT_SECRET"}}  # fmt: skip
    cree = await client.post(f"{ORG}/connectors", json=corps)
    assert cree.status_code == 201, cree.text


async def _publier(client: AsyncClient, pid: str, effet: str = "connector.call") -> Any:
    return await client.put(
        f"/api/v1/projects/{pid}/workflows/onboarding", json={"yaml": ARRIVEE.replace("EFFET", effet)}
    )


async def _ticket(client: AsyncClient, pid: str, champs: dict[str, Any]) -> dict[str, Any]:
    cree = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Arrivée de Léa", "workflow": "onboarding", "fields": champs},
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def test_un_champ_change_valide_par_son_workflow_et_l_interpreteur_l_apprend(
    client: AsyncClient, project: dict[str, Any], graph: Any, app: Any
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    pid = project["id"]
    assert (await _publier(client, pid)).status_code == 200
    ticket = await _ticket(client, pid, {"upn": "lea@acme.test", "date_arrivee": "2026-11-12T09:00:00Z"})
    base = f"/api/v1/work-items/{ticket['id']}"

    deplace = await client.patch(
        base, json={"fields": {"date_arrivee": "2026-11-30T09:00:00Z", "poste": "p14"}}
    )
    assert deplace.status_code == 200, deplace.text
    attendus = {"upn": "lea@acme.test", "date_arrivee": "2026-11-30T09:00:00Z", "poste": "p14"}
    assert deplace.json()["fields"] == attendus
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    assert (ticket["temporal_wf_id"], "fields_changed", {"fields": attendus}) in fake.signals

    retire = await client.patch(base, json={"fields": {"poste": None}})
    assert retire.status_code == 200 and "poste" not in retire.json()["fields"], "`null` retire un champ"
    refus = await client.patch(base, json={"fields": {"date_arrivee": 20261130}})
    assert refus.status_code == 422 and refus.json()["errors"][0]["loc"] == ["fields", "date_arrivee"]
    requis = await client.patch(base, json={"fields": {"upn": None}})
    assert requis.status_code == 422, "un champ requis ne se retire pas"

    await client.post(f"{ORG}/members", json={"email": "lecteur@varga.dev", "role": "viewer"})
    lecteur = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(lecteur, "lecteur@varga.dev")
        assert (await lecteur.patch(base, json={"fields": {"poste": "p16"}})).status_code == 403
    finally:
        await lecteur.aclose()


async def test_un_effet_inconnu_se_refuse_a_la_publication(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    refus = await _publier(client, project["id"], effet="effacer.tout")
    assert refus.status_code == 422, refus.text
    assert refus.json()["errors"][0]["code"] == "action.effect_unknown"
    assert "effacer.tout" in refus.text


async def test_une_operation_que_le_projet_s_interdit_ne_se_propose_pas_et_ne_s_execute_pas(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effets import ContexteEffet, EffetRefuse, effet

    pid = project["id"]
    await _annuaire(client)
    serre = await client.put(
        f"/api/v1/projects/{pid}/operations/entra-acme/create_user", json={"policy": "forbidden"}
    )
    assert serre.status_code == 200, serre.text
    appel = {"connector": "entra-acme", "operation": "create_user",
             "arguments": {"upn": "lea@acme.test", "display_name": "Léa"}}  # fmt: skip
    propose = await client.post(
        f"/api/v1/projects/{pid}/actions",
        json={"kind": "x", "title": "x", "effects": [{"effect": "connector.call", "with": appel}]},
    )
    assert propose.status_code == 422 and "forbidden in this project" in propose.text
    async with session_scope() as session:
        projet = await session.get(Project, pid)
        assert projet is not None
        action = Action(
            org_id=projet.org_id,
            project_id=pid,
            origin="transition",
            kind="x",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        with pytest.raises(EffetRefuse, match="is forbidden to this project"):
            await effet("connector.call")(ContexteEffet(session, action, projet), appel)
    assert graph.comptes == {}, "approuvée ou non, une opération que le projet s'interdit ne part pas"


async def test_une_operation_reservee_a_d_autres_groupes_est_interdite_a_ce_projet(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    pid = project["id"]
    await _annuaire(client)
    reservee = await client.patch(
        f"{ORG}/connectors/entra-acme/operations/create_user", json={"groups": ["rh"]}
    )
    assert reservee.status_code == 200, reservee.text
    appel = {"connector": "entra-acme", "operation": "create_user",
             "arguments": {"upn": "lea@acme.test", "display_name": "Léa"}}  # fmt: skip
    propose = await client.post(
        f"/api/v1/projects/{pid}/actions",
        json={"kind": "x", "title": "x", "effects": [{"effect": "connector.call", "with": appel}]},
    )
    assert propose.status_code == 422 and "entra-acme/create_user: forbidden" in propose.text


async def _proposer_depuis_la_transition(pid: str, item_id: str, tentative: int = 1, **action: Any) -> Any:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.actions import proposer_pour_une_transition
    from choregos_core.dsl import parse_workflow

    spec = parse_workflow(ARRIVEE.replace("EFFET", "connector.call"))[0].transitions[0].action
    assert spec is not None
    document = {**spec.model_dump(mode="json", by_alias=True, exclude_none=True), **action}
    async with session_scope() as session:
        projet, item = await session.get(Project, pid), await session.get(WorkItem, item_id)
        assert projet is not None and item is not None
        nouvelle = await proposer_pour_une_transition(
            session, projet, item, document, transition="t-comptes", tentative=tentative, workflow_id="wi-x"
        )
        return nouvelle.id, nouvelle.status, nouvelle.decisions, nouvelle.title


async def test_la_politique_decide_quand_tout_est_permis_sinon_une_personne(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    pid = project["id"]
    await _annuaire(client)
    assert (await _publier(client, pid)).status_code == 200
    ticket = await _ticket(client, pid, {"upn": "lea@acme.test"})

    identifiant, statut, decisions, titre = await _proposer_depuis_la_transition(pid, ticket["id"])
    assert (statut, decisions, titre) == ("pending_approval", [], "Les comptes de lea@acme.test"), (
        "une écriture est `approval` par défaut : une personne décide"
    )
    assert (await _proposer_depuis_la_transition(pid, ticket["id"]))[0] == identifiant, (
        "la même tentative retrouve la même action : une activité rejouée n'en crée pas une autre"
    )
    ouverte = await client.patch(
        f"{ORG}/connectors/entra-acme/operations/create_user", json={"policy": "allowed"}
    )
    assert ouverte.status_code == 200
    autre, statut, decisions, _ = await _proposer_depuis_la_transition(pid, ticket["id"], tentative=2)
    assert autre != identifiant and statut == "approved" and decisions[0]["by"] == "policy"
    exigee = {"approval": {"approvers": [{"role": "project_owner", "min": 1}]}}
    _, statut, _, _ = await _proposer_depuis_la_transition(pid, ticket["id"], tentative=3, **exigee)
    assert statut == "pending_approval", "le workflow peut ajouter une validation, jamais en retirer"
    # Ce qui la défait est une écriture aussi, jouée sans autre décision : elle compte.
    compensee = [
        {
            "effect": "connector.call",
            "with": {"connector": "entra-acme", "operation": "create_user", "arguments": {"upn": "x"}},
            "compensate": {
                "effect": "connector.call",
                "with": {"connector": "entra-acme", "operation": "disable_user", "arguments": {"upn": "x"}},
            },
        }
    ]
    _, statut, _, _ = await _proposer_depuis_la_transition(pid, ticket["id"], tentative=4, effects=compensee)
    assert statut == "pending_approval", "disable_user est `approval` : la compensation l'impose"


def test_un_gabarit_qui_sort_du_bac_a_sable_est_un_refus_pas_une_panne() -> None:
    from choregos_api.effets import EffetRefuse, rendre

    for gabarit in ("{{ fields.__class__.__mro__ }}", "{{ fields.upn"):
        with pytest.raises(EffetRefuse, match="template refused"):
            rendre(gabarit, {"fields": {"upn": "lea@acme.test"}})


async def test_un_rejet_previent_le_ticket_qui_attend(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    from choregos_api.temporal import get_temporal

    pid = project["id"]
    await _annuaire(client)
    assert (await _publier(client, pid)).status_code == 200
    ticket = await _ticket(client, pid, {"upn": "lea@acme.test"})
    identifiant, *_ = await _proposer_depuis_la_transition(pid, ticket["id"])
    rejet = await client.post(
        f"/api/v1/projects/{pid}/actions/{identifiant}/decision",
        json={"decision": "reject", "reason": "Léa ne vient plus"},
    )
    assert rejet.status_code == 200 and rejet.json()["status"] == "rejected", rejet.text
    # L'interpréteur que la proposition a consigné : c'est lui qui attend (S20-06).
    attendu = ("wi-x", "action_settled", {"action_id": identifiant, "status": "rejected"})
    assert attendu in get_temporal().signals  # type: ignore[attr-defined]
