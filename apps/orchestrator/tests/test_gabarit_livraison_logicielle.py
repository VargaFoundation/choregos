"""Le gabarit `github-software-delivery` traversé (S21-23), sur les faux, avec un VRAI `ReleaseTrain`.

`dev-simple` va du ticket à la production vérifiée sans humain ; `dev-complex` attend ses trois
décisions humaines, plus le capitaine du train ; `study` fusionne un ADR MADR 4 et ne part nulle part ;
un ADR sans décision ne passe pas. Les agents qui jouent sont ceux du catalogue, installés à la
naissance du projet : chaque run nomme son agent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio

DEPOT = "varga/sandbox"

ADR_PROPOSE = """+---
+status: "proposed"
+---
+# Use a message queue for invoices
+
+## Context and Problem Statement
+
+Invoices are sent synchronously (src/billing/send.py:42) and time out under load.
+
+## Considered Options
+
+* A message queue
+* A retry loop
+
+## Decision Outcome
+
+Chosen option: "A message queue", because it absorbs the bursts measured in the research.
"""


async def _projet_dev(setup: Fixture) -> str:
    """Un second projet de l'organisation, né du gabarit : ses workflows, sa politique, ses agents."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        project = Project(
            org_id=org.id,
            slug="dev",
            name="Development",
            status="active",
            template_ref="github-software-delivery@1.0.0",
            config={
                "slug": "dev",
                "org": "varga",
                "repo": {"url": f"https://github.com/{DEPOT}.git", "default_branch": "main"},
                "notify": {"slack_channel": "#dev"},
            },
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        projet_id = str(project.id)
    setup.adapters.cd.set_health("dev", "Healthy")
    return projet_id


async def _ticket(setup: Fixture, projet_id: str, numero: int, workflow: str) -> tuple[str, str]:
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core.domain import WorkItemData
    from sqlalchemy import select

    cle = f"{DEPOT}#{numero}"
    async with session_scope() as session:
        definition = (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == projet_id,
                    WorkflowDef.name == workflow,
                    WorkflowDef.is_active.is_(True),
                )
            )
        ).scalar_one()
        item = WorkItem(
            project_id=projet_id,
            tracker_key=cle,
            title=f"ticket {numero}",
            body_snapshot="The invoice total ignores credits.",
            state="inbox",
            workflow_def_id=definition.id,
        )
        session.add(item)
        await session.flush()
        item_id = str(item.id)
    setup.adapters.tracker.items[cle] = WorkItemData(key=cle, title=f"ticket {numero}", state="Todo")
    return item_id, cle


def _agents(resultats: dict[str, Callable[[], Any]]) -> Callable[[Any], Any]:
    """Le faux exécuteur rend, pour chaque transition, le résultat que son agent rendrait."""

    def jouer(stage_input: Any) -> Any:
        fabrique = resultats.get(stage_input.transition.id)
        assert fabrique is not None, f"aucun résultat scripté pour {stage_input.transition.id}"
        return fabrique()

    return jouer


def _verdict(nom: str, verdict: str = "approve", **sorties: Any) -> Callable[[], Any]:
    return lambda: stage_result(f"{nom}: {verdict}", outputs={"verdict": verdict, **sorties})


def _verificateur_de_prod() -> Any:
    from choregos_contracts import Evidence

    preuve = Evidence(tests_passed=True, tests_run=3, facts={"smoke_ok": True})
    return stage_result("go: error rate unchanged", outputs={"verdict": "approve"}, evidence=preuve)


async def _demarrer(env: Any, setup: Fixture, projet_id: str, item_id: str, cle: str) -> Any:
    from choregos_api.temporal import interpreter_id

    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {"project_id": projet_id, "project_slug": "dev", "work_item_id": item_id, "tracker_key": cle},
        id=interpreter_id("dev", cle),
        task_queue="test",
    )


async def _jusqu_a(handle: Any, requete: str, predicat: Any, timeout: float = 60.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        etat = await handle.query(requete)
        if predicat(etat):
            return dict(etat)
        await asyncio.sleep(0.1)
    raise AssertionError(f"jamais atteint (dernier : {await handle.query(requete)})")


async def _etat(ticket: Any, etat: str) -> None:
    await _jusqu_a(ticket, "status", lambda s: s["state"] == etat)


async def _decider(ticket: Any, approuve: bool = True, raison: str = "") -> None:
    await _jusqu_a(ticket, "status", lambda s: bool(s["pending_request"]))
    decision = {"approved": approuve, "decided_by": "vincent", "kind": "approval", "reason": raison}
    await ticket.signal("human_decision", decision)
    await _jusqu_a(ticket, "status", lambda s: not s["pending_request"])


async def _ci(setup: Fixture, ticket: Any, conclusion: str, livraison: str) -> None:
    """La PR existe ; GitHub Actions rend son verdict, et le webhook le porte au ticket."""
    for _ in range(600):
        if setup.adapters.scm.prs:
            break
        await asyncio.sleep(0.1)
    pr = next(iter(setup.adapters.scm.prs.values())).ref
    setup.adapters.scm.set_checks(pr, conclusion, scanners=False)
    charge = {"conclusion": conclusion}
    evenement = {
        "type": "scm.check.completed",
        "source": "github",
        "delivery_id": livraison,
        "payload": charge,
    }
    await ticket.signal("inbound", evenement)


async def _train(env: Any, nom: str) -> tuple[Any, dict[str, Any]]:
    """Le train que le ticket a demandé (un signal en mémoire sous les faux), lancé pour de vrai."""
    from choregos_orchestrator.train_client import fake_signals

    train_id = f"train-dev-{nom}"
    for _ in range(600):
        if fake_signals(train_id):
            break
        await asyncio.sleep(0.1)
    ((signal, embarquement),) = fake_signals(train_id)
    assert signal == "merged"
    handle = await env.client.start_workflow(
        "ReleaseTrain", {"project_slug": "dev", "env": nom}, id=train_id, task_queue="test"
    )
    charge = {k: v for k, v in embarquement.items() if k != "start_payload"}
    await handle.signal("merged", charge)
    await handle.signal("depart_now", {"by": "vincent"})
    return handle, charge


async def _runs(item_id: str) -> list[tuple[str, str]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = (
            await session.execute(select(Run).where(Run.work_item_id == item_id).order_by(Run.created_at))
        ).scalars()
        return [(str(r.transition_id), str(r.agent_slug)) for r in lignes]


async def _demandes(item_id: str) -> list[str]:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = (
            await session.execute(
                select(HumanRequest)
                .where(HumanRequest.work_item_id == item_id)
                .order_by(HumanRequest.requested_at)
            )
        ).scalars()
        return [str(r.transition_id) for r in lignes]


async def _releases(env: str) -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release).where(Release.env == env))).scalars())


# ───────────────────────── dev-simple ─────────────────────────


async def test_dev_simple_va_du_ticket_a_la_production_verifiee_sans_humain(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 7, "dev-simple")
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/7", [("src/billing/total.py", 12, 3)])
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": lambda: stage_result("small", outputs={"size": "S", "risk": "low"}),
            "t-implement": lambda: stage_result("fixed", artifacts={"branch": "choregos/7"}),
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "412 passed"}),
            "t-review": _verdict("review", review_markdown="Looks right."),
            "t-verify-prod": _verificateur_de_prod,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
            await _etat(ticket, "in_pr")
            await _ci(setup, ticket, "success", "ci-7")
            train, embarquement = await _train(temporal_env, "prod")
            assert embarquement["approval"] == {"required": False, "group": None}
            issue = await asyncio.wait_for(ticket.result(), timeout=90)
            await train.signal("abort", {"by": "test"})
            await train.result()

    assert issue["state"] == "verified"
    assert await _demandes(item_id) == [], "aucune décision humaine"
    (release,) = await _releases("prod")
    assert release.status == "done" and release.approved_by is None, "le train est parti seul"
    assert [agent for _, agent in await _runs(item_id)] == [
        "triager",
        "developer",
        "tester",
        "reviewer",
        "prod-verifier",
    ]


async def test_dev_simple_arrete_un_ticket_trop_gros_au_triage(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Un ticket L n'est pas un petit correctif : un humain le migre vers `dev-complex`."""
    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 8, "dev-simple")
    setup.adapters.executor.handler = _agents(
        {"t-triage": lambda: stage_result("big", outputs={"size": "L", "risk": "medium"})}
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
            await _etat(ticket, "needs_human")
            await ticket.cancel()
    assert await _runs(item_id) == [("t-triage", "triager")], "un seul triage, pas de développement"


# ───────────────────────── dev-complex ─────────────────────────


async def test_dev_complex_attend_trois_decisions_humaines_et_le_capitaine(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """La spec, la PR, et — la CI ayant rougi APRÈS l'approbation, le réparateur ayant poussé — la PR
    encore : ce qui a changé n'a pas été vu. Puis staging sans approbation, la prod sous le capitaine."""
    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 9, "dev-complex")
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/9", [("src/billing/queue.py", 120, 4)])
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": lambda: stage_result("medium", outputs={"size": "M", "risk": "medium"}),
            "t-specify": lambda: stage_result(
                "spec", outputs={"spec_markdown": "## Spec", "allowed_paths": ["src/**", "tests/**"]}
            ),
            "t-plan": lambda: stage_result("plan", outputs={"plan_markdown": "1. queue"}),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/9"}),
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "420 passed"}),
            "t-review": _verdict("review", review_markdown="Fine."),
            "t-security-review": _verdict("security", security_review_markdown="No exposure."),
            "t-release-notes": lambda: stage_result(
                "notes", outputs={"release_notes_markdown": "- Invoices are queued."}
            ),
            "t-fix-ci": lambda: stage_result("flaky import fixed", artifacts={"branch": "choregos/9"}),
            "t-verify-prod": _verificateur_de_prod,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
            await _etat(ticket, "awaiting_spec_approval")
            await _decider(ticket)
            await _etat(ticket, "awaiting_pr_approval")
            await _decider(ticket)
            await _ci(setup, ticket, "failure", "ci-9-rouge")
            await _etat(ticket, "awaiting_pr_approval")
            await _jusqu_a(ticket, "status", lambda s: bool(s["pending_request"]))
            await _ci(setup, ticket, "success", "ci-9-vert")
            await _decider(ticket)

            staging, embarquement = await _train(temporal_env, "staging")
            assert embarquement["approval"] == {"required": False, "group": None}
            await _etat(ticket, "staged")
            prod, embarquement = await _train(temporal_env, "prod")
            assert embarquement["approval"] == {"required": True, "group": "release-captains"}
            await _jusqu_a(prod, "status_query", lambda s: s["status"] == "awaiting_approval")
            assert (await ticket.query("status"))["state"] == "staged", "la prod attend son capitaine"
            await prod.signal("approve", {"by": "marie"})
            issue = await asyncio.wait_for(ticket.result(), timeout=90)
            for train in (staging, prod):
                await train.signal("abort", {"by": "test"})
                await train.result()

    assert issue["state"] == "verified"
    assert await _demandes(item_id) == ["t-approve-spec", "t-approve-pr", "t-approve-pr"]
    (release,) = await _releases("prod")
    assert release.approved_by == "marie"
    (staging_release,) = await _releases("staging")
    assert staging_release.approved_by is None
    transitions = [t for t, _ in await _runs(item_id)]
    assert transitions.count("t-fix-ci") == 1, transitions
    corps = next(iter(setup.adapters.scm.prs.values())).body
    assert "## Release notes\n- Invoices are queued." in corps, "les notes de version sont dans la PR"


# ───────────────────────── study ─────────────────────────


def _etude(adr: str) -> dict[str, Callable[[], Any]]:
    return {
        "t-frame": lambda: stage_result("question", outputs={"spec_markdown": "Which queue?"}),
        "t-research": lambda: stage_result("options", outputs={"research_markdown": "Two options."}),
        "t-draft": lambda: stage_result(
            "ADR drafted", outputs={"adr_path": "docs/adr/0001-queue.md", "adr_markdown": adr}
        ),
        "t-critique": _verdict("critique", review_markdown="Sound."),
        "t-accept": lambda: stage_result("ADR accepted"),
    }


async def test_study_fusionne_un_adr_madr_approuve_et_ne_part_nulle_part(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.train_client import fake_signals

    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 11, "study")
    adr = ("docs/adr/0001-queue.md", 17, 0, ADR_PROPOSE)
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/11", [adr])
    setup.adapters.executor.handler = _agents(_etude(ADR_PROPOSE))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
            await _etat(ticket, "awaiting_decision")
            # L'architecte passera le statut à `accepted` : la branche le portera.
            accepte = ADR_PROPOSE.replace('status: "proposed"', 'status: "accepted"\n+date: 2026-10-07')
            setup.adapters.scm.set_diff(DEPOT, "main", "choregos/11", [(adr[0], 18, 0, accepte)])
            await _decider(ticket)
            await _etat(ticket, "in_pr")
            await _ci(setup, ticket, "success", "ci-11")
            issue = await asyncio.wait_for(ticket.result(), timeout=90)

    assert issue["state"] == "adopted"
    assert not [w for w in fake_signals() if w.startswith("train-dev")], (
        "une étude n'embarque dans aucun train"
    )
    assert await _demandes(item_id) == ["t-decide"]
    assert [a for _, a in await _runs(item_id)] == [
        "spec-writer",
        "researcher",
        "architect",
        "reviewer",
        "architect",
    ]


async def test_un_adr_sans_decision_outcome_ne_passe_pas(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    projet_id = await _projet_dev(setup)
    item_id, cle = await _ticket(setup, projet_id, 12, "study")
    # La phrase de décision est là, pas sa section : c'est le gabarit MADR 4 qui manque, pas l'idée.
    sans_decision = ADR_PROPOSE.replace("+## Decision Outcome\n+\n", "")
    assert 'Chosen option: "' in sans_decision and "Decision Outcome" not in sans_decision
    setup.adapters.scm.set_diff(
        DEPOT, "main", "choregos/12", [("docs/adr/0001-queue.md", 13, 0, sans_decision)]
    )
    setup.adapters.executor.handler = _agents(_etude(sans_decision))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, projet_id, item_id, cle)
            await _etat(ticket, "needs_human")
            await ticket.cancel()
    runs = [t for t, _ in await _runs(item_id)]
    assert runs.count("t-draft") == 3 and "t-critique" not in runs, runs
