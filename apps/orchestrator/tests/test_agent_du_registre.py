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
from .kits.registry_agent import agent_stage_plan, install_agent


async def test_les_instructions_de_l_agent_remplacent_le_playbook_dans_leur_cadre(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from choregos_playbooks import OUTPUT_CONTRACT, playbook_source

    await install_agent(setup)
    prepare = await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    entree = prepare["stage_input"]
    prompt = entree["playbook"]["prompt"]
    assert "Tu coordonnes l'arrivée décrite dans « Les avoirs ne sont pas déduits du total »" in prompt
    assert OUTPUT_CONTRACT in prompt and "this frame cannot be changed" in prompt
    # Le playbook du rôle `implement` existe : il perd.
    assert playbook_source("implement").splitlines()[0] not in prompt
    assert entree["playbook"]["ref"].startswith("agent:coordinateur@1@sha256:")
    assert entree["budget"]["usd"] <= 0.5 and entree["budget"]["max_turns"] == 7
    async with session_scope() as session:
        run = await session.get(Run, prepare["run_id"])
        assert run is not None and (run.agent_slug, run.agent_version) == ("coordinateur", 1)


async def test_l_epingle_du_projet_resserre_le_budget(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, pin={"budget": {"run_usd": 0.2}})
    entree = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert entree["budget"]["usd"] <= 0.2


@pytest.mark.parametrize(
    ("agent", "motif"),
    [
        pytest.param({"status": "revoked"}, "is revoked", id="revoque"),
        pytest.param({"lifecycle": "retired"}, "is retired", id="retired"),
        pytest.param({"lifecycle": "draft"}, "is a draft", id="draft"),
        pytest.param({"slug": "autre"}, "does not exist", id="inconnu"),
        pytest.param(
            {"instructions": "{{ ''.__class__.__mro__ }}"}, "do not render", id="evasion-du-bac-a-sable"
        ),
    ],
)
async def test_ce_qui_ne_part_pas_le_dit_sans_reessayer(
    setup: Fixture, agent: dict[str, Any], motif: str
) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, **agent)
    with pytest.raises(ApplicationError, match=motif) as refus:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refus.value.non_retryable


@pytest.mark.parametrize(
    ("agent", "motif"),
    [
        pytest.param({"scope": "personal"}, "personal agent", id="personal"),
        pytest.param({"scope": "project", "project": "other"}, "another project", id="another-projects"),
    ],
)
async def test_an_agent_that_may_not_run_here_stops_the_run_without_a_retry(
    setup: Fixture, agent: dict[str, Any], motif: str
) -> None:
    """Whatever got the name into the workflow (the publication check is bypassed here), the run
    refuses a personal agent and another project's agent (ADR 0061)."""
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, **agent)
    with pytest.raises(ApplicationError, match=motif) as refus:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refus.value.non_retryable


async def test_a_project_agent_runs_in_its_own_project(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, scope="project", project="this")
    entree = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert entree["playbook"]["ref"].startswith("agent:coordinateur@1@sha256:")


async def test_l_interpreteur_transmet_l_agent_que_l_acteur_nomme(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """De bout en bout : un workflow dont l'acteur nomme un agent ; le run porte son nom."""
    from choregos_api.db.models import Run, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow
    from sqlalchemy import select

    await install_agent(setup)
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

    await install_agent(setup)  # budget du jour : 5 $
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
    with pytest.raises(ApplicationError, match="daily budget") as refus:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refus.value.non_retryable and refus.value.type == "admission_refused"


async def test_les_skills_de_l_agent_partent_dans_le_stage_input_avec_leur_empreinte(setup: Fixture) -> None:
    from choregos_api.db.models import AgentVersion, Project, Skill, SkillVersion
    from choregos_api.db.session import session_scope
    from choregos_contracts import empreinte_de_skill
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    fichiers = {"SKILL.md": "---\nname: procedure-onboarding\ndescription: x\n---\n"}
    await install_agent(setup)
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
    entree = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert entree["skills"] == [
        {"slug": "procedure-onboarding", "version": 2, "digest": empreinte_de_skill(fichiers)}
    ], "la dernière version, à défaut d'une version dite"


async def _knowledge(setup: Fixture, slug: str, **fields: Any) -> None:
    """A knowledge base of the project's organisation, with one ready and one failed document."""
    from choregos_api.db.models import KnowledgeBase, KnowledgeDocument, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        base = KnowledgeBase(org_id=projet.org_id, slug=slug, name=slug.title(), **fields)
        session.add(base)
        await session.flush()
        for name, status, markdown in (("zoning.pptx", "ready", "# Zoning\n"), ("scan.pdf", "failed", None)):
            session.add(
                KnowledgeDocument(
                    org_id=projet.org_id,
                    knowledge_base_id=base.id,
                    filename=name,
                    title=name,
                    mime="x",
                    size_bytes=1,
                    digest=f"sha256:{slug}-{name}",
                    markdown=markdown,
                    status=status,
                )
            )


async def _reads(slugs: list[str]) -> None:
    from choregos_api.db.models import AgentVersion
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        version = (await session.execute(select(AgentVersion))).scalar_one()
        version.spec = {**version.spec, "knowledge": [{"slug": s} for s in slugs]}


async def test_the_knowledge_bases_of_the_agent_are_pinned_in_the_stage_input(setup: Fixture) -> None:
    """S33-02: prepare_stage passes the agent's bases — its ready documents and their digest."""
    from choregos_contracts import knowledge_digest
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup)
    await _knowledge(setup, "architecture", scope="org", projects=[], groups=[], serving_mode="files")
    await _knowledge(setup, "big-corpus", scope="org", projects=[], groups=[], serving_mode="index")
    await _reads(["architecture", "big-corpus"])
    entree = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    files, index = entree["knowledge"]
    assert (files["slug"], files["mode"], len(files["documents"])) == ("architecture", "files", 1)
    assert files["digest"] == knowledge_digest(
        [{"path": "zoning.md", "title": "zoning.pptx", "filename": "zoning.pptx", "markdown": "# Zoning\n"}]
    ), "only the ready document, under its file name"
    assert (index["slug"], index["mode"], index["documents"]) == ("big-corpus", "index", [])


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"scope": "projects", "projects": ["another-project"], "groups": []}, "not served to the project"),
        ({"scope": "groups", "projects": [], "groups": ["auditors"]}, "not served to the project"),
        ({"scope": "org", "projects": [], "groups": [], "status": "archived"}, "status `archived`"),
        (None, "does not exist"),
    ],
)
async def test_a_base_the_project_is_not_served_stops_the_run(
    setup: Fixture, fields: dict[str, Any] | None, reason: str
) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup)
    if fields is not None:
        await _knowledge(setup, "architecture", **fields)
    await _reads(["architecture"])
    with pytest.raises(ApplicationError, match=reason) as refusal:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refusal.value.non_retryable and refusal.value.type == "KnowledgeRefused"


async def test_a_restricted_base_is_pinned_only_for_an_initiator_who_reads_it(setup: Fixture) -> None:
    """S42-11e (ADR 0097): the run acts for the person who started the work item."""
    from choregos_api.db.models import Membership, Project, User, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup)
    await _knowledge(setup, "hr", scope="org", projects=[], groups=[], serving_mode="files", restricted=True)
    await _reads(["hr"])

    async def started_by(initiated_by: str) -> None:
        async with session_scope() as session:
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.initiated_by = initiated_by

    await started_by("schedule:nightly")
    with pytest.raises(ApplicationError, match="acts for nobody") as refusal:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refusal.value.non_retryable and refusal.value.type == "KnowledgeRefused"

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        user = User(email="hr-lead@example.org", display_name="HR lead")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=projet.org_id, role="org_admin"))
    await started_by("person:hr-lead@example.org")
    entree = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert [ref["slug"] for ref in entree["knowledge"]] == ["hr"]
    # What the run writes now lands under a work item read only by the base's readers (ADR 0114).
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None and item.restricted_knowledge == ["hr"]


async def test_a_run_carries_the_latest_approved_skill_version_never_one_pending_review(
    setup: Fixture,
) -> None:
    """S32-06: a high-risk version waits for a second reviewer; unpinned, the run reads the latest
    APPROVED one; pinned to the pending one, the run does not start."""
    from choregos_api.db.models import AgentVersion, Project, Skill, SkillVersion
    from choregos_api.db.session import session_scope
    from choregos_contracts import empreinte_de_skill
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select
    from temporalio.exceptions import ApplicationError

    fichiers = {"SKILL.md": "---\nname: fill-forms\ndescription: x\n---\n"}
    await install_agent(setup)
    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        skill = Skill(org_id=projet.org_id, slug="fill-forms", status="active")
        session.add(skill)
        await session.flush()
        for version, etat in ((1, "approved"), (2, "pending_review")):
            session.add(
                SkillVersion(
                    skill_id=skill.id,
                    org_id=projet.org_id,
                    version=version,
                    files=fichiers,
                    digest=empreinte_de_skill(fichiers),
                    review_status=etat,
                )
            )
        version_de_l_agent = (await session.execute(select(AgentVersion))).scalar_one()
        version_de_l_agent.spec = {**version_de_l_agent.spec, "skills": [{"slug": "fill-forms"}]}
    entree = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert [ref["version"] for ref in entree["skills"]] == [1]

    async with session_scope() as session:
        version_de_l_agent = (await session.execute(select(AgentVersion))).scalar_one()
        version_de_l_agent.spec = {
            **version_de_l_agent.spec,
            "skills": [{"slug": "fill-forms", "version": 2}],
        }
    with pytest.raises(ApplicationError, match="second reviewer"):
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur", attempt=2))


async def _memory_of_the_run(
    setup: Fixture, scope: str | None, attempt: int
) -> tuple[dict[str, Any], list[str]]:
    from choregos_api.db.models import AgentVersion, Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    async with session_scope() as session:
        version = (await session.execute(select(AgentVersion))).scalar_one()
        spec = {k: v for k, v in version.spec.items() if k != "memory"}
        version.spec = spec if scope is None else {**spec, "memory": scope}
    prepared = await prepare_stage(agent_stage_plan(setup, agent="coordinateur", attempt=attempt))
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        subjects = [m["subject"] for m in (run.context_pack or {}).get("memories", [])]
    return prepared["stage_input"], subjects


async def test_the_agent_memory_scope_decides_what_the_run_reads(setup: Fixture) -> None:
    """S32-02: `project` reads the project's memory (as before), `agent` its own within the
    project, `none` nothing — and the memory server, which serves the project's memory, is only
    handed to a run whose scope is the project."""
    from choregos_core import memory_namespace

    await install_agent(setup)
    setup.adapters.memory.seed(
        setup.project_slug, "convention", "project:convention", "shared by the project"
    )
    setup.adapters.memory.seed(
        memory_namespace(setup.project_slug, "agent", "coordinateur"),
        "convention",
        "agent:convention",
        "the coordinator's own lesson",
    )

    entree, lues = await _memory_of_the_run(setup, None, attempt=1)
    assert entree["memory_scope"] == "project" and lues == ["project:convention"], "unchanged by default"

    entree, lues = await _memory_of_the_run(setup, "agent", attempt=2)
    assert entree["memory_scope"] == "agent" and lues == ["agent:convention"]
    assert "memory" not in entree["tools"]["mcp"]

    entree, lues = await _memory_of_the_run(setup, "none", attempt=3)
    assert entree["memory_scope"] == "none" and lues == []


async def test_the_instructions_read_the_fields_of_the_work_item(setup: Fixture) -> None:
    """A child opened by a fan-out holds there what its parent measured for it (S38-15): the agent's
    instructions render them, as the work item stores them."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.fields = {"symbol": "TRND.US", "product": {"relevant": True, "dsr": 0.9731}}
    await install_agent(setup, instructions="Fields: {{ ticket.fields | tojson }}")
    prepare = await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    prompt = prepare["stage_input"]["playbook"]["prompt"]
    assert '"symbol": "TRND.US"' in prompt and "0.9731" in prompt, prompt
