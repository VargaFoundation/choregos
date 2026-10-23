"""A run recalls from every memory its agent version binds, and writes to one (S40-11b, ADR 0075)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from choregos_core.domain import Fact, Provenance

from .conftest import Fixture

SEC = "org:varga#memory:security-lessons"
ARCH = "org:varga#memory:architecture-decisions"
DECISIONS = "billing-api#memory:decisions"
SHARED = "billing-api"
FULL = [
    {"scope": "global", "slug": "security-lessons", "mode": "read"},
    {"scope": "global", "slug": "architecture-decisions", "mode": "read"},
    {"scope": "project", "slug": "decisions", "mode": "read"},
    {"scope": "project", "slug": "shared", "mode": "write"},
]


async def _stores(setup: Fixture) -> None:
    """Global: security-lessons (org), architecture-decisions (billing-api), elsewhere-only (checkout-web),
    old-lessons (archived). Project: decisions."""
    from choregos_api.db.models import MemoryStore, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        org_id = project.org_id
        rows = [
            ("security-lessons", "global", "org", "org", [], "active"),
            ("architecture-decisions", "global", "projects", "org", ["billing-api"], "active"),
            ("elsewhere-only", "global", "projects", "org", ["checkout-web"], "active"),
            ("old-lessons", "global", "org", "org", [], "archived"),
        ]
        for slug, scope, reach, owner, projects, status in rows:
            session.add(
                MemoryStore(
                    org_id=org_id,
                    slug=slug,
                    display_name=slug,
                    scope=scope,
                    owner_key=owner,
                    reach=reach,
                    projects=projects,
                    status=status,
                )
            )
        session.add(
            MemoryStore(
                org_id=org_id,
                slug="decisions",
                display_name="decisions",
                scope="project",
                project_id=project.id,
                owner_key=project.id,
                projects=["billing-api"],
                status="active",
            )
        )


async def _agent(
    setup: Fixture, memories: list[dict[str, Any]] | None, slug: str = "architect", **spec: Any
) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id, slug=slug, kind="internal", display_name=slug, status="active", scope="org"
        )
        session.add(agent)
        await session.flush()
        body: dict[str, Any] = {
            "instructions": "Review {{ ticket.title }} and report.",
            "model": "profile:standard",
            "budget": {"run_usd": 0.5},
            **spec,
        }
        if memories is not None:
            body["memories"] = memories
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=body, checksum="v1")
        )


def _plan(setup: Fixture, agent: str = "architect", attempt: int = 1) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": attempt,
        "agent": agent,
    }


async def _seed(setup: Fixture) -> None:
    memory = setup.adapters.memory
    for namespace, subject, content in (
        (SEC, "tls", "avoirs need TLS 1.3 for the credit service"),
        (SEC, "secrets", "avoirs never log secrets"),
        (ARCH, "adr-7", "avoirs follow the ledger pattern"),
        (DECISIONS, "rounding", "avoirs round half up"),
        (SHARED, "naming", "avoirs are called credit notes"),
    ):
        await memory.write_fact(namespace, Fact(kind="convention", subject=subject, content=content))


async def test_two_global_and_two_project_memories_are_resolved_in_order_and_merged(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _stores(setup)
    await _seed(setup)
    await _agent(setup, FULL)
    prepared = await prepare_stage(_plan(setup))
    stage_input = prepared["stage_input"]
    assert [(m["label"], m["mode"], m["namespace"]) for m in stage_input["memories"]] == [
        ("global:security-lessons", "read", SEC),
        ("global:architecture-decisions", "read", ARCH),
        ("project:decisions", "read", DECISIONS),
        ("project:shared", "write", SHARED),
    ]
    assert stage_input["memory_scope"] == "none", "no legacy value means this list: old runners read nothing"
    assert "memory" not in stage_input["tools"]["mcp"], "Ecphoria's own server only serves the shared memory"
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        items = [(i["memory"], i["subject"]) for i in run.context_pack["memories"]]
    # Round-robin by precedence: the best of each memory first, then the second of the first.
    assert [label for label, _ in items[:4]] == [
        "global:security-lessons",
        "global:architecture-decisions",
        "project:decisions",
        "project:shared",
    ]
    assert items[0] == ("global:security-lessons", "secrets") and items[4] == (
        "global:security-lessons",
        "tls",
    )
    assert len(items) == 5


async def test_a_binding_that_does_not_resolve_is_skipped_journaled_once_and_the_run_starts(
    setup: Fixture,
) -> None:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    await _stores(setup)
    await _seed(setup)
    bound = [
        {"scope": "global", "slug": "security-lessons", "mode": "read"},
        {"scope": "global", "slug": "elsewhere-only", "mode": "read"},
        {"scope": "global", "slug": "old-lessons", "mode": "read"},
        {"scope": "global", "slug": "never-created", "mode": "read"},
        {"scope": "project", "slug": "not-in-this-project", "mode": "read"},
        {"scope": "project", "slug": "shared", "mode": "write"},
    ]
    await _agent(setup, bound)
    prepared = await prepare_stage(_plan(setup))
    assert [m["label"] for m in prepared["stage_input"]["memories"]] == [
        "global:security-lessons",
        "project:shared",
    ]
    await prepare_stage(_plan(setup))  # a replay finds the run again
    async with session_scope() as session:
        events = (
            (
                await session.execute(
                    select(RunEvent.payload).where(
                        RunEvent.run_id == prepared["run_id"], RunEvent.type == "memory.skipped"
                    )
                )
            )
            .scalars()
            .all()
        )
    reasons = {e["memory"]: e["reason"] for e in events}
    assert len(events) == 4, "one event per skipped binding, once"
    assert "does not reach project billing-api" in reasons["global:elsewhere-only"]
    assert "archived" in reasons["global:old-lessons"]
    assert "does not exist" in reasons["global:never-created"]
    assert "no memory `not-in-this-project`" in reasons["project:not-in-this-project"]


async def test_a_memory_that_raises_leaves_the_others_in_the_pack(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    await _stores(setup)
    await _seed(setup)
    await _agent(setup, FULL)
    memory = setup.adapters.memory
    original = memory.context_pack

    async def flaky(namespace: str, *args: Any, **kwargs: Any) -> Any:
        if namespace == ARCH:
            raise RuntimeError("memory down")
        return await original(namespace, *args, **kwargs)

    memory.context_pack = flaky
    prepared = await prepare_stage(_plan(setup))
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        labels = {i["memory"] for i in run.context_pack["memories"]}
    assert labels == {"global:security-lessons", "project:decisions", "project:shared"}


async def test_the_legacy_single_memory_is_unchanged(setup: Fixture) -> None:
    """No `memories`, no `memory`: the project's shared memory, its pack unmerged and unlabelled, and the
    memory server announced (what S32-02 shipped)."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage

    await _seed(setup)
    await _agent(setup, None)
    prepared = await prepare_stage(_plan(setup))
    stage_input = prepared["stage_input"]
    assert stage_input["memory_scope"] == "project"
    assert [(m["label"], m["mode"], m["namespace"]) for m in stage_input["memories"]] == [
        ("project:shared", "write", SHARED)
    ]
    async with session_scope() as session:
        run = await session.get(Run, prepared["run_id"])
        assert run is not None
        assert [i["memory"] for i in run.context_pack["memories"]] == [None]


def test_the_memory_server_is_announced_only_for_the_legacy_shared_memory() -> None:
    from choregos_orchestrator.activities.stage import _serveurs_mcp

    settings = SimpleNamespace(url_du_sidecar_d_outils="", memory_url="http://ecphoria:8432")
    assert "memory" in _serveurs_mcp(settings, "project")
    assert "memory" not in _serveurs_mcp(settings, "none"), "several, global or own: not the project's server"
    assert "memory" not in _serveurs_mcp(settings, "agent")


async def _run_of(setup: Fixture, memories: list[dict[str, Any]], run_id: str = "run-1") -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    resolved = [
        {
            "label": f"{m['scope']}:{m['slug']}",
            "scope": m["scope"],
            "slug": m["slug"],
            "mode": m["mode"],
            "namespace": {"security-lessons": SEC, "architecture-decisions": ARCH, "shared": SHARED}[
                m["slug"]
            ],
        }
        for m in memories
    ]
    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="failed",
                agent_slug="architect",
                result={"reason": "tests"},
                stage_input={"memory_scope": "none", "memories": resolved},
            )
        )
    return run_id


async def test_a_lesson_goes_to_the_write_target(setup: Fixture) -> None:
    from choregos_orchestrator.activities.memory import write_run_lesson

    await _stores(setup)
    run_id = await _run_of(
        setup,
        [
            {"scope": "global", "slug": "security-lessons", "mode": "read"},
            {"scope": "project", "slug": "shared", "mode": "write"},
        ],
    )
    done = await write_run_lesson(
        {"project_slug": setup.project_slug, "run_id": run_id, "subject": "retry", "content": "retry once"}
    )
    assert done == {"written": True}
    memory = setup.adapters.memory
    assert [m.content for m in memory.facts[SHARED]] == ["retry once"]
    assert SEC not in memory.facts and SEC not in memory.pending


async def test_a_run_with_no_write_binding_writes_nothing(setup: Fixture) -> None:
    from choregos_orchestrator.activities.memory import write_run_lesson

    await _stores(setup)
    run_id = await _run_of(setup, [{"scope": "global", "slug": "security-lessons", "mode": "read"}])
    done = await write_run_lesson(
        {"project_slug": setup.project_slug, "run_id": run_id, "subject": "x", "content": "x"}
    )
    assert done["written"] is False
    assert not setup.adapters.memory.facts and not setup.adapters.memory.pending


async def test_a_global_write_target_receives_proposals_not_facts(setup: Fixture) -> None:
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.memory import ingest_sources, write_run_lesson

    await _stores(setup)
    run_id = await _run_of(
        setup,
        [
            {"scope": "project", "slug": "shared", "mode": "read"},
            {"scope": "global", "slug": "architecture-decisions", "mode": "write"},
        ],
    )
    lesson = await write_run_lesson(
        {"project_slug": setup.project_slug, "run_id": run_id, "subject": "retry", "content": "retry once"}
    )
    assert lesson == {"written": False, "proposed": True}
    async with session_scope() as session:
        session.add(
            Finding(
                project_id=setup.project_id,
                origin_run_id=run_id,
                title="proposed memory: ledger",
                type="docs",
                severity="low",
                evidence="use the ledger pattern",
                status="created",
            )
        )
    await ingest_sources({"project_slug": setup.project_slug, "sources": ["findings", "run_lessons"]})
    await ingest_sources({"project_slug": setup.project_slug, "sources": ["findings", "run_lessons"]})
    memory = setup.adapters.memory
    assert ARCH not in memory.facts, (
        "a global memory holds nothing an agent wrote until an administrator accepts"
    )
    subjects = sorted(m.subject for m in memory.pending[ARCH])
    assert len(subjects) == len(set(subjects)) == 3, (
        f"lesson, finding and failed-run lesson, once each: {subjects}"
    )
    assert SHARED not in memory.pending


async def test_auto_accept_covers_the_projects_memories_and_never_a_global_one(setup: Fixture) -> None:
    from choregos_orchestrator.activities.memory import accept_pending_facts

    await _stores(setup)
    await _run_of(setup, [{"scope": "project", "slug": "shared", "mode": "write"}])
    memory = setup.adapters.memory
    namespaces = [SHARED, "billing-api#agent:architect", DECISIONS, ARCH]
    for namespace in namespaces:
        for n in range(2):  # the policy's threshold: proposed by 2 runs
            await memory.propose_fact(
                namespace,
                Fact(kind="run_lesson", subject="same", content="the same lesson"),
                Provenance(source="agent", run_id=f"r{n}"),
            )
    done = await accept_pending_facts({"project_slug": setup.project_slug})
    assert done["accepted"] == 6
    for namespace in namespaces[:3]:
        assert len(memory.facts[namespace]) == 2 and not memory.pending[namespace]
    assert ARCH not in memory.facts and len(memory.pending[ARCH]) == 2, "a global memory waits for a person"


async def test_journaling_the_same_skip_twice_leaves_one_event(setup: Fixture) -> None:
    """The guard itself, apart from the early return of a replayed `prepare_stage`."""
    from choregos_api.db.models import Run, RunEvent
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import _journal_skipped_memories
    from sqlalchemy import select

    await _run_of(setup, [{"scope": "project", "slug": "shared", "mode": "write"}], run_id="run-skip")
    skipped = [{"memory": "global:gone", "reason": "the memory does not exist"}]
    for _ in range(2):
        async with session_scope() as session:
            assert await session.get(Run, "run-skip") is not None
            await _journal_skipped_memories(session, "run-skip", skipped)
    async with session_scope() as session:
        events = (
            (
                await session.execute(
                    select(RunEvent).where(RunEvent.run_id == "run-skip", RunEvent.type == "memory.skipped")
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 1


async def test_a_renamed_organisation_keeps_its_global_namespaces_at_run_time(setup: Fixture) -> None:
    """S40-24: the run resolves `org:<memory key>`, not `org:<current slug>`, so a slug change does not
    hide the facts already written."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import prepare_stage
    from sqlalchemy import select

    await _stores(setup)
    await _seed(setup)
    await _agent(setup, FULL)
    async with session_scope(orgs="*") as session:
        org = (await session.execute(select(Organization))).scalar_one()
        org.settings = {**(org.settings or {}), "memory_key": "varga"}
        org.slug = "diametral"
        project = await session.get(Project, setup.project_id)
        assert project is not None
    prepared = await prepare_stage(_plan(setup))
    namespaces = {m["label"]: m["namespace"] for m in prepared["stage_input"]["memories"]}
    assert namespaces["global:security-lessons"] == SEC
    assert namespaces["global:architecture-decisions"] == ARCH
