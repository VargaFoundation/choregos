"""Le parcours d'un ticket se lit en une fois, et chaque déplacement nomme son arête (S22-01).

La console animait la vie d'un ticket en relisant les titres de sa chronologie (« a → b ») : rien ne
disait par quelle transition il était passé, ni si une revue l'avait renvoyé ou une personne refusé. La
route `/work-items/{id}/journey` rend la carte de la version où il est épinglé, ses déplacements
rattachés à leur arête, et les pas de chaque acteur — runs, demandes, actions, départs du train.
"""

from __future__ import annotations

import pathlib
from datetime import timedelta
from typing import Any

import pytest
from choregos_core import parse_workflow, utcnow
from httpx import AsyncClient

from .conftest import login

RACINE = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def dev_complex() -> Any:
    texte = (RACINE / "templates/github-software-delivery/workflows/dev-complex.yaml").read_text()
    workflow, _ = parse_workflow(texte)
    return workflow


@pytest.mark.parametrize(
    ("de", "vers", "raison", "attendu"),
    [
        (None, "inbox", "started", ("start", None)),
        ("implemented", "tested", "", ("nominal", "t-test")),
        ("implemented", "planned", "failing gates: evidence_present", ("retry", "t-test")),
        ("tested", "addressing_review", "", ("changes_requested", "t-review")),
        ("reviewed", "addressing_review", "", ("changes_requested", "t-security-review")),
        ("addressing_review", "implemented", "", ("nominal", "t-address-review")),
        (
            "awaiting_pr_approval",
            "addressing_review",
            "human decision: sent back",
            ("reject", "t-approve-pr"),
        ),
        ("pr_approved", "fixing_ci", "failing gates: ci_green", ("retry", "t-merge")),
        ("planned", "needs_human", "3 attempts exhausted", ("escalate", "t-implement")),
        ("integrated", "staged", "deployment verified", ("nominal", "t-stage")),
        ("needs_human", "planned", "answered", ("resume", None)),
        ("needs_human", "abandoned", "", ("nominal", "t-abandon")),
        ("planned", "implemented", "migrated to dev-complex@2", ("migrated", None)),
        ("inbox", "verified", "moved by hand in the tracker", ("other", None)),
    ],
)
def test_chaque_deplacement_nomme_l_arete_qui_l_a_porte(
    dev_complex: Any, de: str | None, vers: str, raison: str, attendu: tuple[str, str | None]
) -> None:
    from choregos_api.services import arete_du_deplacement

    assert arete_du_deplacement(dev_complex, de, vers, raison) == attendu


def test_un_defaut_depuis_tout_etat_d_agent_se_reconnait(dev_complex: Any) -> None:
    from choregos_api.services import arete_du_deplacement

    # `tested` n'a pas d'escalade vers `needs_human` par sa transition : c'est le défaut `on_question`.
    sans_escalade = dev_complex.model_copy(deep=True)
    for t in sans_escalade.transitions:
        if t.key == "t-review":
            t.on_changes_requested = None
    assert arete_du_deplacement(sans_escalade, "tested", "needs_human", "a question") == ("default", None)


LIVRAISON = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: livraison, version: 1}
initial: inbox
actors:
  dev: {type: agent, role: implement}
  relecteur: {type: agent, role: review, fresh_context: true}
  repondant: {type: agent, role: address_review}
  mainteneurs: {type: human, group: maintainers}
  plateforme: {type: system}
states:
  inbox: {display: To do, kind: wait}
  implemented: {display: Implemented}
  reviewed: {display: Reviewed}
  addressing: {display: Addressing the review}
  approved: {display: Approved}
  staged: {display: In staging}
  done: {display: Done, terminal: true}
  needs_human: {display: Needs a human, kind: wait}
transitions:
  - id: t-impl
    from: inbox
    to: implemented
    by: dev
    on_fail: {to: inbox, max_attempts: 2, escalate_to: needs_human}
  - id: t-review
    from: implemented
    to: reviewed
    by: relecteur
    outputs: [verdict]
    on_changes_requested: {to: addressing, max_attempts: 2, escalate_to: needs_human}
  - id: t-address
    from: addressing
    to: implemented
    by: repondant
    on_fail: {to: addressing, max_attempts: 2, escalate_to: needs_human}
  - {id: t-approve, from: reviewed, to: approved, by: mainteneurs, on_reject: addressing}
  - {id: t-stage, from: approved, to: staged, via: release_train, train: {env: staging}}
  - {id: t-close, from: staged, to: done, by: plateforme}
defaults:
  from_any_agent_state: {on_question: needs_human}
  needs_human: {on_abandon: done}
"""


async def _ticket(client: AsyncClient, pid: str) -> dict[str, Any]:
    publie = await client.put(f"/api/v1/projects/{pid}/workflow", json={"yaml": LIVRAISON})
    assert publie.status_code == 200, publie.text
    cree = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Les avoirs", "start": False}
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def _vie_du_ticket(project: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    """Une vie complète, écrite comme l'orchestrateur l'écrit : une revue qui renvoie une fois, une
    approbation humaine, un départ du train, et l'action de la plateforme qui attend encore."""
    from choregos_api.db.models import Action, Event, HumanRequest, Project, Release, Run
    from choregos_api.db.session import session_scope

    debut = utcnow() - timedelta(hours=3)
    instant = iter(debut + timedelta(minutes=10 * i) for i in range(1, 40))
    ids: dict[str, str] = {}

    async with session_scope(orgs="*") as session:
        projet = await session.get(Project, project["id"])
        assert projet is not None

        def deplacer(de: str | None, vers: str, raison: str = "") -> None:
            session.add(
                Event(
                    project_id=project["id"],
                    work_item_id=item["id"],
                    type="choregos.workitem.state_changed",
                    subject=item["tracker_key"],
                    payload={"from": de, "to": vers, "reason": raison},
                    ts=next(instant),
                )
            )

        def courir(transition: str, role: str, tentative: int, verdict: str | None = None) -> Run:
            depart = next(instant)
            run = Run(
                work_item_id=item["id"],
                project_id=project["id"],
                transition_id=transition,
                stage_role=role,
                attempt=tentative,
                actor={"t-impl": "dev", "t-review": "relecteur", "t-address": "repondant"}[transition],
                model="profile:standard",
                status="succeeded",
                started_at=depart,
                ended_at=depart + timedelta(minutes=4),
                cost_usd=0.5,
                result={
                    "summary": f"{role}, tour {tentative}",
                    "outputs": {"verdict": verdict} if verdict else {},
                    "evidence": {"tests_run": 412, "tests_failed": 0, "lint": "ok", "coverage_delta": None},
                },
            )
            session.add(run)
            return run

        deplacer(None, "inbox", "started")
        courir("t-impl", "implement", 1)
        deplacer("inbox", "implemented", "credit notes deducted")
        revue = courir("t-review", "review", 1, "changes_requested")
        deplacer("implemented", "addressing", "two remarks")
        courir("t-address", "address_review", 1)
        deplacer("addressing", "implemented")
        courir("t-review", "review", 2, "approve")
        deplacer("implemented", "reviewed")
        demande = HumanRequest(
            work_item_id=item["id"],
            project_id=project["id"],
            transition_id="t-approve",
            kind="approval",
            payload={"summary": "Approve the pull request"},
            requested_at=next(instant),
            decided_at=next(instant),
            decided_by="marie@varga.dev",
            decision={"kind": "approval", "approved": True, "reason": "looks good"},
        )
        session.add(demande)
        deplacer("reviewed", "approved", "human decision: approved")
        release = Release(
            project_id=project["id"],
            env="staging",
            batch_no=7,
            status="verified",
            items=[{"work_item_key": item["tracker_key"], "title": item["title"]}],
            started_at=next(instant),
            ended_at=next(instant),
        )
        session.add(release)
        # Une release qui ne l'emporte pas n'est pas un pas de ce ticket.
        session.add(
            Release(
                project_id=project["id"],
                env="staging",
                batch_no=8,
                status="verified",
                items=[{"work_item_key": "AUTRE-9"}],
                started_at=next(instant),
            )
        )
        deplacer("approved", "staged", "deployment verified")
        action = Action(
            org_id=projet.org_id,
            project_id=project["id"],
            work_item_id=item["id"],
            origin="transition",
            kind="close.ticket",
            title="Close the work item",
            proposed_by={"kind": "system", "id": "system", "via": "transition", "transition": "t-close"},
            status="pending_approval",
        )
        session.add(action)
        await session.flush()
        ids["revue-1"], ids["demande"], ids["release"], ids["action"] = (
            revue.id,
            demande.id,
            release.id,
            action.id,
        )
    return ids


async def test_le_parcours_rattache_chaque_deplacement_et_chaque_pas_a_sa_transition(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _ticket(client, project["id"])
    ids = await _vie_du_ticket(project, item)

    reponse = await client.get(f"/api/v1/work-items/{item['id']}/journey")
    assert reponse.status_code == 200, reponse.text
    parcours = reponse.json()

    assert (parcours["workflow_name"], parcours["workflow_version"], parcours["initial"]) == (
        "livraison",
        1,
        "inbox",
    )
    assert {n["id"] for n in parcours["graph"]["nodes"]} >= {"inbox", "addressing", "staged"}
    assert [e["id"] for e in parcours["process"]][:2] == ["t-impl", "t-review"]
    assert [(m["from"], m["to"], m["kind"], m["transition_id"]) for m in parcours["moves"]] == [
        (None, "inbox", "start", None),
        ("inbox", "implemented", "nominal", "t-impl"),
        ("implemented", "addressing", "changes_requested", "t-review"),
        ("addressing", "implemented", "nominal", "t-address"),
        ("implemented", "reviewed", "nominal", "t-review"),
        ("reviewed", "approved", "nominal", "t-approve"),
        ("approved", "staged", "nominal", "t-stage"),
    ]

    pas = parcours["steps"]
    assert [(p["kind"], p["transition_id"], p["attempt"]) for p in pas] == [
        ("agent", "t-impl", 1),
        ("agent", "t-review", 1),
        ("agent", "t-address", 1),
        ("agent", "t-review", 2),
        ("human", "t-approve", 1),
        ("action", "t-stage", 1),
        ("action", "t-close", 1),
    ], "dans l'ordre du temps, la release qui ne l'emporte pas en moins"
    revues = [p for p in pas if p["transition_id"] == "t-review"]
    assert [r["verdict"] for r in revues] == ["changes_requested", "approve"]
    assert revues[0]["id"] == ids["revue-1"] and revues[1]["summary"] == "review, tour 2"
    assert revues[0]["evidence"] == {"tests_run": 412, "tests_failed": 0, "lint": "ok"}, (
        "les preuves nulles se taisent"
    )
    humain = next(p for p in pas if p["kind"] == "human")
    assert (humain["id"], humain["actor"], humain["status"], humain["decided_by"]) == (
        ids["demande"],
        "mainteneurs",
        "approved",
        "marie@varga.dev",
    )
    train = next(p for p in pas if p["transition_id"] == "t-stage")
    assert (train["id"], train["status"], train["actor"]) == (ids["release"], "verified", "release_train")
    attente = pas[-1]
    assert (attente["id"], attente["status"], attente["actor"]) == (
        ids["action"],
        "pending_approval",
        "platform",
    )
    assert (parcours["state"], parcours["closed"]) == (item["state"], False)


async def test_le_parcours_garde_la_carte_ou_le_ticket_est_epingle(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _ticket(client, project["id"])
    from choregos_core import template_yaml

    autre = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": template_yaml("default-simple")}
    )
    assert autre.status_code == 200, autre.text
    parcours = (await client.get(f"/api/v1/work-items/{item['id']}/journey")).json()
    assert parcours["workflow_name"] == "livraison"
    assert "addressing" in {n["id"] for n in parcours["graph"]["nodes"]}, (
        "la carte du ticket, pas celle du projet"
    )


async def test_une_demande_humaine_en_attente_se_voit_et_un_inconnu_ne_lit_rien(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    item = await _ticket(client, project["id"])
    async with session_scope(orgs="*") as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=project["id"],
                transition_id="t-approve",
                kind="approval",
                payload={"question": "Ship it?"},
                requested_at=utcnow(),
                due_at=utcnow() + timedelta(hours=24),
            )
        )
    (pas,) = (await client.get(f"/api/v1/work-items/{item['id']}/journey")).json()["steps"]
    assert (pas["status"], pas["summary"], pas["ended_at"]) == ("waiting", "Ship it?", None)
    assert pas["due_at"] is not None

    assert (await client.get("/api/v1/work-items/inconnu/journey")).status_code == 404
    # Un compte sans aucun rôle dans l'organisation : la connexion de développement en pose un, on le retire.
    await login(client, "intrus@ailleurs.dev")
    from choregos_api.db.models import Membership, User
    from sqlalchemy import delete, select

    async with session_scope(orgs="*") as session:
        intrus = (await session.execute(select(User).where(User.email == "intrus@ailleurs.dev"))).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == intrus.id))
    # Sur PostgreSQL, la RLS cache déjà le ticket à ce compte (404) ; sur SQLite, c'est la garde de la
    # route qui répond (403). Les deux disent la même chose : un inconnu ne lit rien — jamais 200.
    assert (await client.get(f"/api/v1/work-items/{item['id']}/journey")).status_code in {403, 404}
