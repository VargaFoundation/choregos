"""Un acteur nomme un agent du registre (ADR 0033, S18-02).

Les instructions de l'agent remplacent le playbook ; elles sont rendues dans un bac à sable, puis
cadrées par le contrat de sortie et les invariants, que leur auteur ne peut pas retirer. Sa version
— l'épinglée du projet, surcharges appliquées — fixe modèle, limites et budget ; le run la nomme.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture, stage_result

INSTRUCTIONS = "Tu coordonnes l'arrivée décrite dans « {{ ticket.title }} » : comptes, groupes, matériel."


async def _agent(
    setup: Fixture,
    *,
    slug: str = "coordinateur",
    status: str = "active",
    instructions: str = INSTRUCTIONS,
    epingle: dict[str, Any] | None = None,
) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project, ProjectAgent
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        agent = Agent(org_id=projet.org_id, slug=slug, kind="internal", display_name=slug, status=status)
        session.add(agent)
        await session.flush()
        spec = {
            "instructions": instructions,
            "model": "profile:standard",
            "limits": {"max_turns": 7, "max_minutes": 20},
            "budget": {"run_usd": 0.5, "daily_usd": 5.0},
            "skills": [],
            "mcp_servers": [],
        }
        session.add(
            AgentVersion(agent_id=agent.id, org_id=projet.org_id, version=1, spec=spec, checksum="sha256:v1")
        )
        if epingle is not None:
            session.add(
                ProjectAgent(
                    project_id=setup.project_id,
                    org_id=projet.org_id,
                    agent_id=agent.id,
                    version=1,
                    overrides=epingle,
                )
            )


def _plan(setup: Fixture, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
        **extra,
    }


async def test_les_instructions_de_l_agent_remplacent_le_playbook_dans_leur_cadre(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from choregos_playbooks import OUTPUT_CONTRACT, playbook_source

    await _agent(setup)
    prepare = await prepare_stage(_plan(setup, agent="coordinateur"))
    entree = prepare["stage_input"]
    prompt = entree["playbook"]["prompt"]
    assert "Tu coordonnes l'arrivée décrite dans « Les avoirs ne sont pas déduits du total »" in prompt
    assert OUTPUT_CONTRACT in prompt and "ce cadre ne se modifie pas" in prompt
    # Le playbook du rôle `implement` existe : il perd.
    assert playbook_source("implement").splitlines()[0] not in prompt
    assert entree["playbook"]["ref"].startswith("agent:coordinateur@1@sha256:")
    assert entree["budget"]["usd"] <= 0.5 and entree["budget"]["max_turns"] == 7
    async with session_scope() as session:
        run = await session.get(Run, prepare["run_id"])
        assert run is not None and (run.agent_slug, run.agent_version) == ("coordinateur", 1)


async def test_l_epingle_du_projet_resserre_le_budget(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _agent(setup, epingle={"budget": {"run_usd": 0.2}})
    entree = (await prepare_stage(_plan(setup, agent="coordinateur")))["stage_input"]
    assert entree["budget"]["usd"] <= 0.2


@pytest.mark.parametrize(
    ("agent", "motif"),
    [
        pytest.param({"status": "revoked"}, "revoked", id="revoque"),
        pytest.param({"slug": "autre"}, "n'existe pas", id="inconnu"),
        pytest.param(
            {"instructions": "{{ ''.__class__.__mro__ }}"}, "ne se rendent pas", id="evasion-du-bac-a-sable"
        ),
    ],
)
async def test_ce_qui_ne_part_pas_le_dit_sans_reessayer(
    setup: Fixture, agent: dict[str, Any], motif: str
) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _agent(setup, **agent)
    with pytest.raises(ApplicationError, match=motif) as refus:
        await prepare_stage(_plan(setup, agent="coordinateur"))
    assert refus.value.non_retryable


async def test_l_interpreteur_transmet_l_agent_que_l_acteur_nomme(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """De bout en bout : un workflow dont l'acteur nomme un agent ; le run porte son nom."""
    from choregos_api.db.models import Run, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow
    from sqlalchemy import select

    await _agent(setup)
    document = {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {"name": "arrivee", "version": 1},
        "initial": "demande",
        "actors": {"coordinateur": {"type": "agent", "role": "implement", "agent": "coordinateur"}},
        "states": {"demande": {"display": "Demande"}, "fait": {"display": "Fait", "terminal": True}},
        "transitions": [{"id": "t-coordonner", "from": "demande", "to": "fait", "by": "coordinateur"}],
    }
    async with session_scope() as session:
        ligne = WorkflowDef(
            project_id=setup.project_id,
            name="arrivee",
            version=1,
            source="platform",
            yaml="",
            json_doc=Workflow.model_validate(document).model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            checksum="sha256:arrivee",
            is_active=True,
        )
        session.add(ligne)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state = ligne.id, "demande"
    setup.adapters.executor.queue_result(stage_result("arrivée préparée"))
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "work_item_id": setup.work_item_id,
                "tracker_key": setup.tracker_key,
            },
            id=f"wi-{setup.project_slug}-agent",
            task_queue="test",
        )
        resultat = await handle.result()
    assert resultat["state"] == "fait"
    async with session_scope() as session:
        runs = (
            (await session.execute(select(Run).where(Run.work_item_id == setup.work_item_id))).scalars().all()
        )
        assert [(r.agent_slug, r.agent_version) for r in runs] == [("coordinateur", 1)]


async def test_un_agent_qui_a_epuise_son_budget_du_jour_ne_lance_plus_de_run(setup: Fixture) -> None:
    from choregos_api.db.models import CostLedger, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.stage import prepare_stage

    await _agent(setup)  # budget du jour : 5 $
    async with session_scope() as session:
        session.add(
            Run(
                id="run-hier",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                agent_slug="coordinateur",
                agent_version=1,
            )
        )
        await session.flush()
        session.add(
            CostLedger(
                project_id=setup.project_id, run_id="run-hier", kind="model", cost_usd=4.0, ts=utcnow()
            )
        )
        session.add(
            CostLedger(project_id=setup.project_id, run_id="run-hier", kind="tool", cost_usd=1.5, ts=utcnow())
        )
    with pytest.raises(ApplicationError, match="budget du jour") as refus:
        await prepare_stage(_plan(setup, agent="coordinateur"))
    assert refus.value.non_retryable and refus.value.type == "admission_refused"


async def test_les_skills_de_l_agent_partent_dans_le_stage_input_avec_leur_empreinte(setup: Fixture) -> None:
    from choregos_api.db.models import AgentVersion, Project, Skill, SkillVersion
    from choregos_api.db.session import session_scope
    from choregos_contracts import empreinte_de_skill
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    fichiers = {"SKILL.md": "---\nname: procedure-onboarding\ndescription: x\n---\n"}
    await _agent(setup)
    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        skill = Skill(org_id=projet.org_id, slug="procedure-onboarding", status="active")
        session.add(skill)
        await session.flush()
        for version in (1, 2):
            session.add(
                SkillVersion(
                    skill_id=skill.id,
                    org_id=projet.org_id,
                    version=version,
                    files=fichiers,
                    digest=empreinte_de_skill(fichiers),
                )
            )
        version_de_l_agent = (await session.execute(select(AgentVersion))).scalar_one()
        version_de_l_agent.spec = {**version_de_l_agent.spec, "skills": [{"slug": "procedure-onboarding"}]}
    entree = (await prepare_stage(_plan(setup, agent="coordinateur")))["stage_input"]
    assert entree["skills"] == [
        {"slug": "procedure-onboarding", "version": 2, "digest": empreinte_de_skill(fichiers)}
    ], "la dernière version, à défaut d'une version dite"
