"""An agent declares the workspace it needs (S51-03, ADR 0119): `none`, `read` or `write`.

On the production tenant an analysis agent that only reads a ticket got the whole repository, the
write scope `**` and `github.com` on its network: every stage of every agent cloned the project's
repository. A version now says what it needs, and the run gets that and no more.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

ITEM_SCOPE = ["src/orders/**", "tests/orders/**"]


async def _network(setup: Fixture, *, project: dict[str, Any]) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        policy = policy_model(row)
        for key, value in project.items():
            setattr(policy.sandbox.network, key, value)
        row.json_doc = policy.model_dump(mode="json", exclude_none=True)


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


async def _agent(setup: Fixture, **spec: Any) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        agent = Agent(
            org_id=project.org_id,
            slug="analyst",
            kind="internal",
            display_name="analyst",
            status="active",
            scope="org",
        )
        session.add(agent)
        await session.flush()
        body = {"instructions": "Analyse the ticket.", "model": "profile:standard", **spec}
        session.add(
            AgentVersion(agent_id=agent.id, org_id=project.org_id, version=1, spec=body, checksum="sha256:v1")
        )


async def _prepared(setup: Fixture, **extra: Any) -> dict[str, Any]:
    from choregos_orchestrator.activities.stage import prepare_stage

    return dict((await prepare_stage(_plan(setup, **extra)))["stage_input"])


async def _recorded_scope(setup: Fixture) -> list[str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        run = await session.get(Run, f"{setup.work_item_id}-t-implement-1")
        assert run is not None
        return list(run.allowed_paths or [])


async def test_an_agent_that_declares_no_workspace_clones_nothing_and_reaches_no_repository_host(
    setup: Fixture,
) -> None:
    await _network(setup, project={"allow_domains": ["pypi.org"]})
    await _agent(setup, workspace="none")
    stage_input = await _prepared(setup, agent="analyst")
    assert stage_input["repo"] is None, "nothing to clone, nothing to push"
    assert stage_input["permissions"]["allow_domains"] == ["pypi.org"], "no github.com without a clone"


async def test_an_agent_without_a_workspace_needs_the_repository_host_from_the_project(
    setup: Fixture,
) -> None:
    """The repository's host is no longer implied: an agent asking for it must find it allowed."""
    await _network(setup, project={"allow_domains": ["pypi.org"]})
    await _agent(setup, workspace="none", network={"domains": ["github.com"]})
    with pytest.raises(ApplicationError, match=r"github\.com") as refused:
        await _prepared(setup, agent="analyst")
    assert refused.value.type == "AgentUnavailable" and refused.value.non_retryable


async def test_an_agent_that_reads_clones_the_repository_but_writes_none_of_it(setup: Fixture) -> None:
    await _network(setup, project={"allow_domains": ["pypi.org"]})
    await _agent(setup, workspace="read")
    stage_input = await _prepared(setup, agent="analyst")
    assert stage_input["repo"] is not None and stage_input["repo"]["url"].startswith("https://github.com/")
    assert "github.com" in stage_input["permissions"]["allow_domains"], "a clone reaches its host"
    # Never `[]`: an empty scope is "no scope declared, write allowed" to the runner's guards.
    assert stage_input["allowed_paths"] == [".choregos/outputs/**"]
    assert stage_input["permissions"]["write_paths"] == [".choregos/outputs/**"]
    assert await _recorded_scope(setup) == [".choregos/outputs/**"], "a scope change is judged on it"


@pytest.mark.parametrize("declared", [None, "write"])
async def test_an_agent_that_writes_or_says_nothing_runs_as_before(
    setup: Fixture, declared: str | None
) -> None:
    await _network(setup, project={"allow_domains": ["pypi.org"]})
    await _agent(setup, **({"workspace": declared} if declared else {}))
    stage_input = await _prepared(setup, agent="analyst")
    assert stage_input["repo"] is not None
    assert stage_input["permissions"]["allow_domains"] == ["pypi.org", "github.com"]
    assert stage_input["allowed_paths"] == ITEM_SCOPE
    assert stage_input["permissions"]["write_paths"] == ITEM_SCOPE


async def test_a_trial_runs_with_the_drafts_workspace(setup: Fixture) -> None:
    await _network(setup, project={"allow_domains": ["pypi.org"]})
    draft = {"instructions": "Try me.", "workspace": "none"}
    stage_input = await _prepared(setup, trial_spec=draft, trial_budget_usd=0.5)
    assert stage_input["repo"] is None
    assert stage_input["permissions"]["allow_domains"] == ["pypi.org"]


async def test_the_diff_gates_pass_on_a_run_that_never_cloned(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No branch (the SCM says 404), and whatever the agent wrote in its empty directory is no
    change to the repository: `diff_size_max files: 0` passes instead of "diff unavailable"."""
    from choregos_adapters.errors import UpstreamError
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import StageResult, StageStatus
    from choregos_orchestrator.activities.gates import evaluate_gates

    async def compare(repo: str, base: str, head: str) -> Any:
        raise UpstreamError("github", f"compare {base}...{head}", status_code=404)

    monkeypatch.setattr(setup.adapters.scm, "compare", compare)
    await _network(setup, project={"allow_domains": ["pypi.org"]})
    await _agent(setup, workspace="none")
    await _prepared(setup, agent="analyst")
    result = StageResult(status=StageStatus.DONE, summary="Analysed")
    # The runner's local git measured the notes the agent left in its empty directory.
    result.evidence.diff_files, result.evidence.diff_lines = 1, 12
    result.evidence.measured = ["diff_files", "diff_lines"]
    result.artifacts.commits = ["0ddba11"]
    async with session_scope() as session:
        run = await session.get(Run, f"{setup.work_item_id}-t-implement-1")
        assert run is not None
        run.result = result.model_dump(mode="json", by_alias=True)
        run_id = run.id

    verdicts = await evaluate_gates(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "run_id": run_id,
            "gates": [{"name": "diff_size_max", "params": {"files": 0, "lines": 0}}],
        }
    )
    assert [v["passed"] for v in verdicts] == [True], verdicts
    assert verdicts[0]["detail"] == "0 files / 0 lines"
