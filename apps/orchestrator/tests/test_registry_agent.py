"""An actor names a registry agent (ADR 0033, S18-02).

The agent's instructions replace the playbook; they are rendered in a sandbox, then framed by the
output contract and the invariants, which their author cannot remove. Its version — the one pinned
by the project, overrides applied — sets model, limits and budget; the run names it.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture, stage_result
from .kits.registry_agent import agent_stage_plan, install_agent


async def test_the_agent_instructions_replace_the_playbook_within_their_frame(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from choregos_playbooks import OUTPUT_CONTRACT, playbook_source

    await install_agent(setup)
    prepare = await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    stage_input = prepare["stage_input"]
    prompt = stage_input["playbook"]["prompt"]
    french = "Tu coordonnes l'arrivée décrite dans « Les avoirs ne sont pas déduits du total »"  # lang: fr-ok
    assert french in prompt
    assert OUTPUT_CONTRACT in prompt and "this frame cannot be changed" in prompt
    # The playbook of the `implement` role exists: it loses.
    assert playbook_source("implement").splitlines()[0] not in prompt
    assert stage_input["playbook"]["ref"].startswith("agent:coordinateur@1@sha256:")
    assert stage_input["budget"]["usd"] <= 0.5 and stage_input["budget"]["max_turns"] == 7
    async with session_scope() as session:
        run = await session.get(Run, prepare["run_id"])
        assert run is not None and (run.agent_slug, run.agent_version) == ("coordinateur", 1)


async def test_the_project_pin_tightens_the_budget(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, pin={"budget": {"run_usd": 0.2}})
    stage_input = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert stage_input["budget"]["usd"] <= 0.2


@pytest.mark.parametrize(
    ("agent", "reason"),
    [
        pytest.param({"status": "revoked"}, "is revoked", id="revoked"),
        pytest.param({"lifecycle": "retired"}, "is retired", id="retired"),
        pytest.param({"lifecycle": "draft"}, "is a draft", id="draft"),
        pytest.param({"slug": "other"}, "does not exist", id="unknown"),
        pytest.param({"instructions": "{{ ''.__class__.__mro__ }}"}, "do not render", id="sandbox-escape"),
    ],
)
async def test_what_cannot_start_says_so_without_retrying(
    setup: Fixture, agent: dict[str, Any], reason: str
) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, **agent)
    with pytest.raises(ApplicationError, match=reason) as refusal:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refusal.value.non_retryable


@pytest.mark.parametrize(
    ("agent", "reason"),
    [
        pytest.param({"scope": "personal"}, "personal agent", id="personal"),
        pytest.param({"scope": "project", "project": "other"}, "another project", id="another-projects"),
    ],
)
async def test_an_agent_that_may_not_run_here_stops_the_run_without_a_retry(
    setup: Fixture, agent: dict[str, Any], reason: str
) -> None:
    """Whatever got the name into the workflow (the publication check is bypassed here), the run
    refuses a personal agent and another project's agent (ADR 0061)."""
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, **agent)
    with pytest.raises(ApplicationError, match=reason) as refusal:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refusal.value.non_retryable


async def test_a_project_agent_runs_in_its_own_project(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup, scope="project", project="this")
    stage_input = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert stage_input["playbook"]["ref"].startswith("agent:coordinateur@1@sha256:")


async def test_the_interpreter_passes_on_the_agent_the_actor_names(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """End to end: a workflow whose actor names an agent; the run carries its name."""
    from choregos_api.db.models import Run, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow
    from sqlalchemy import select

    await install_agent(setup)
    document = {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {"name": "arrival", "version": 1},
        "initial": "request",
        "actors": {"coordinateur": {"type": "agent", "role": "implement", "agent": "coordinateur"}},
        "states": {"request": {"display": "Request"}, "done": {"display": "Done", "terminal": True}},
        "transitions": [{"id": "t-coordinate", "from": "request", "to": "done", "by": "coordinateur"}],
    }
    async with session_scope() as session:
        row = WorkflowDef(
            project_id=setup.project_id,
            name="arrival",
            version=1,
            source="platform",
            yaml="",
            json_doc=Workflow.model_validate(document).model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            checksum="sha256:arrival",
            is_active=True,
        )
        session.add(row)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state = row.id, "request"
    setup.adapters.executor.queue_result(stage_result("arrival prepared"))
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
        result = await handle.result()
    assert result["state"] == "done"
    async with session_scope() as session:
        runs = (
            (await session.execute(select(Run).where(Run.work_item_id == setup.work_item_id))).scalars().all()
        )
        assert [(r.agent_slug, r.agent_version) for r in runs] == [("coordinateur", 1)]


async def test_an_agent_that_spent_its_daily_budget_starts_no_more_runs(setup: Fixture) -> None:
    from choregos_api.db.models import CostLedger, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from choregos_orchestrator.activities.stage import prepare_stage

    await install_agent(setup)  # daily budget: $5
    async with session_scope() as session:
        session.add(
            Run(
                id="run-yesterday",
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
                project_id=setup.project_id, run_id="run-yesterday", kind="model", cost_usd=4.0, ts=utcnow()
            )
        )
        session.add(
            CostLedger(
                project_id=setup.project_id, run_id="run-yesterday", kind="tool", cost_usd=1.5, ts=utcnow()
            )
        )
    with pytest.raises(ApplicationError, match="daily budget") as refusal:
        await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert refusal.value.non_retryable and refusal.value.type == "admission_refused"


async def test_the_agent_skills_go_in_the_stage_input_with_their_digest(setup: Fixture) -> None:
    from choregos_api.db.models import AgentVersion, Project, Skill, SkillVersion
    from choregos_api.db.session import session_scope
    from choregos_contracts import skill_digest
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    files = {"SKILL.md": "---\nname: procedure-onboarding\ndescription: x\n---\n"}
    await install_agent(setup)
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        skill = Skill(org_id=project.org_id, slug="procedure-onboarding", status="active")
        session.add(skill)
        await session.flush()
        for version in (1, 2):
            session.add(
                SkillVersion(
                    skill_id=skill.id,
                    org_id=project.org_id,
                    version=version,
                    files=files,
                    digest=skill_digest(files),
                )
            )
        agent_version = (await session.execute(select(AgentVersion))).scalar_one()
        agent_version.spec = {**agent_version.spec, "skills": [{"slug": "procedure-onboarding"}]}
    stage_input = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert stage_input["skills"] == [
        {"slug": "procedure-onboarding", "version": 2, "digest": skill_digest(files)}
    ], "the latest version, failing a named one"


async def _knowledge(setup: Fixture, slug: str, **fields: Any) -> None:
    """A knowledge base of the project's organisation, with one ready and one failed document."""
    from choregos_api.db.models import KnowledgeBase, KnowledgeDocument, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        base = KnowledgeBase(org_id=project.org_id, slug=slug, name=slug.title(), **fields)
        session.add(base)
        await session.flush()
        for name, status, markdown in (("zoning.pptx", "ready", "# Zoning\n"), ("scan.pdf", "failed", None)):
            session.add(
                KnowledgeDocument(
                    org_id=project.org_id,
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
    stage_input = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    files, index = stage_input["knowledge"]
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
        project = await session.get(Project, setup.project_id)
        assert project is not None
        user = User(email="hr-lead@example.org", display_name="HR lead")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=project.org_id, role="org_admin"))
    await started_by("person:hr-lead@example.org")
    stage_input = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert [ref["slug"] for ref in stage_input["knowledge"]] == ["hr"]
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
    from choregos_contracts import skill_digest
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select
    from temporalio.exceptions import ApplicationError

    files = {"SKILL.md": "---\nname: fill-forms\ndescription: x\n---\n"}
    await install_agent(setup)
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        skill = Skill(org_id=project.org_id, slug="fill-forms", status="active")
        session.add(skill)
        await session.flush()
        for version, review_status in ((1, "approved"), (2, "pending_review")):
            session.add(
                SkillVersion(
                    skill_id=skill.id,
                    org_id=project.org_id,
                    version=version,
                    files=files,
                    digest=skill_digest(files),
                    review_status=review_status,
                )
            )
        agent_version = (await session.execute(select(AgentVersion))).scalar_one()
        agent_version.spec = {**agent_version.spec, "skills": [{"slug": "fill-forms"}]}
    stage_input = (await prepare_stage(agent_stage_plan(setup, agent="coordinateur")))["stage_input"]
    assert [ref["version"] for ref in stage_input["skills"]] == [1]

    async with session_scope() as session:
        agent_version = (await session.execute(select(AgentVersion))).scalar_one()
        agent_version.spec = {
            **agent_version.spec,
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

    stage_input, read_subjects = await _memory_of_the_run(setup, None, attempt=1)
    assert stage_input["memory_scope"] == "project" and read_subjects == ["project:convention"], (
        "unchanged by default"
    )

    stage_input, read_subjects = await _memory_of_the_run(setup, "agent", attempt=2)
    assert stage_input["memory_scope"] == "agent" and read_subjects == ["agent:convention"]
    assert "memory" not in stage_input["tools"]["mcp"]

    stage_input, read_subjects = await _memory_of_the_run(setup, "none", attempt=3)
    assert stage_input["memory_scope"] == "none" and read_subjects == []


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


async def test_the_agent_reads_who_filed_the_work_item_before_its_body(setup: Fixture) -> None:
    """An access request is checked against its requester (#1179): the body the agent reads opens with
    who filed it, and `ticket.reporter` names them on its own."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.body_snapshot = "Level: maintainer"
        item.reporter = "Jean Martin <jmartin@varga.dev>"
    await install_agent(setup, instructions="{{ ticket.body }}\n--\n{{ ticket.reporter }}")
    prepare = await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    prompt = prepare["stage_input"]["playbook"]["prompt"]
    assert "Filed by: Jean Martin <jmartin@varga.dev>\n\nLevel: maintainer\n--\nJean Martin" in prompt, prompt


async def test_a_work_item_without_a_reporter_reads_its_body_unchanged(setup: Fixture) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.body_snapshot = "Level: maintainer"
    await install_agent(setup, instructions="[{{ ticket.body }}]")
    prepare = await prepare_stage(agent_stage_plan(setup, agent="coordinateur"))
    assert "[Level: maintainer]" in prepare["stage_input"]["playbook"]["prompt"]
