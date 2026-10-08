"""Une tâche humaine (S20-06) : un formulaire et une attestation — côté API.

`complete` n'accepte que ce que le formulaire décrit, requis compris, et l'attestation quand la
tâche en demande une ; les valeurs deviennent des champs du ticket (une action suivante les lit),
et la décision garde les valeurs et la phrase attestée, telle qu'elle a été montrée : la preuve.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

BADGE = """
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
      badge_uid: {type: string}
initial: badge
actors:
  accueil: {type: human, group: accueil}
states:
  badge: {display: Badge à remettre, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - id: t-badge
    from: badge
    to: fait
    by: accueil
    task:
      title: Remettre le badge
      form:
        type: object
        required: [badge_uid]
        properties:
          badge_uid: {type: string, minLength: 8}
      attest: J'ai remis le badge en main propre à son porteur
"""
ATTESTEE = "J'ai remis le badge en main propre à son porteur"


async def _ticket_qui_attend(
    client: AsyncClient, pid: str, kind: str = "task", yaml: str = BADGE
) -> dict[str, Any]:
    """Le ticket, et la demande que l'interpréteur aurait créée en arrivant sur la tâche."""
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    publie = await client.put(f"/api/v1/projects/{pid}/workflows/onboarding", json={"yaml": yaml})
    assert publie.status_code == 200, publie.text
    cree = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Arrivée de Léa", "workflow": "onboarding", "fields": {"upn": "lea@acme.test"}},
    )
    assert cree.status_code == 201, cree.text
    ticket = dict(cree.json())
    payload = (
        {"summary": "Remettre le badge", "form": {"type": "object", "required": ["badge_uid"],
         "properties": {"badge_uid": {"type": "string", "minLength": 8}}}, "attest": ATTESTEE}
        if kind == "task"
        else {"summary": "Validation requise"}
    )  # fmt: skip
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=ticket["id"],
                project_id=pid,
                transition_id="t-badge",
                kind=kind,
                payload=payload,
                requested_at=utcnow(),
            )
        )
    return ticket


async def test_une_tache_se_remplit_s_atteste_et_ses_valeurs_deviennent_des_champs(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    ticket = await _ticket_qui_attend(client, project["id"])
    base = f"/api/v1/work-items/{ticket['id']}"
    decisions = f"{base}/decisions"

    approuvee = await client.post(decisions, json={"kind": "approve"})
    assert approuvee.status_code == 422 and "is completed" in approuvee.text
    vide = await client.post(decisions, json={"kind": "complete", "values": {}, "attested": True})
    assert vide.status_code == 422 and "badge_uid" in vide.text, "un champ requis manque"
    court = await client.post(
        decisions, json={"kind": "complete", "values": {"badge_uid": "04A1"}, "attested": True}
    )
    assert court.status_code == 422 and court.json()["errors"][0]["loc"] == ["values", "badge_uid"]
    hors = await client.post(
        decisions,
        json={"kind": "complete", "values": {"badge_uid": "04A1B2C3", "upn": "x"}, "attested": True},
    )
    assert hors.status_code == 422 and "not in the task's form" in hors.text, (
        "une tâche ne remplit que son formulaire"
    )
    sans = await client.post(decisions, json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}})
    assert sans.status_code == 422 and ATTESTEE in sans.text, "l'attestation se demande, mot pour mot"

    faite = await client.post(
        decisions, json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}, "attested": True}
    )
    assert faite.status_code == 202, faite.text
    decision = faite.json()["decision"]
    assert (decision["kind"], decision["approved"]) == ("task", True)
    assert decision["values"] == {"badge_uid": "04A1B2C3"} and decision["attestation"] == ATTESTEE
    assert (await client.get(base)).json()["fields"] == {"upn": "lea@acme.test", "badge_uid": "04A1B2C3"}
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    (envoye,) = [p for _, nom, p in fake.signals if nom == "human_decision"]
    assert envoye["values"] == {"badge_uid": "04A1B2C3"}, "l'interpréteur reçoit les valeurs"
    chronologie = (await client.get(f"{base}/timeline")).text
    assert ATTESTEE in chronologie, "la preuve se lit dans l'histoire du ticket"


async def test_une_tache_impossible_se_renvoie_et_complete_ne_vaut_que_pour_une_tache(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _ticket_qui_attend(client, project["id"], kind="approval")
    decisions = f"/api/v1/work-items/{ticket['id']}/decisions"
    refus = await client.post(decisions, json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}})
    assert refus.status_code == 422 and "only a task is completed" in refus.text

    autre = await _ticket_qui_attend_encore(client, project["id"])
    renvoyee = await client.post(
        f"/api/v1/work-items/{autre}/decisions", json={"kind": "reject", "reason": "lecteur en panne"}
    )
    assert renvoyee.status_code == 202 and renvoyee.json()["decision"]["approved"] is False


async def _ticket_qui_attend_encore(client: AsyncClient, pid: str) -> str:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    cree = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Arrivée de Paul", "workflow": "onboarding", "fields": {"upn": "paul@acme.test"}},
    )
    assert cree.status_code == 201, cree.text
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=cree.json()["id"],
                project_id=pid,
                transition_id="t-badge",
                kind="task",
                payload={"form": {"type": "object", "properties": {"badge_uid": {"type": "string"}}}},
                requested_at=utcnow(),
            )
        )
    return str(cree.json()["id"])


async def test_les_valeurs_d_une_tache_restent_des_champs_valides_par_le_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Le formulaire ne dit pas tout : `metadata.inputs` garde le dernier mot sur un champ."""
    hexa = BADGE.replace(
        "      badge_uid: {type: string}\n", "      badge_uid: {type: string, pattern: '^[0-9A-F]+$'}\n", 1
    )
    assert hexa != BADGE
    ticket = await _ticket_qui_attend(client, project["id"], yaml=hexa)
    refus = await client.post(
        f"/api/v1/work-items/{ticket['id']}/decisions",
        json={"kind": "complete", "values": {"badge_uid": "zz-pas-hexa"}, "attested": True},
    )
    assert refus.status_code == 422 and refus.json()["errors"][0]["loc"] == ["fields", "badge_uid"]
