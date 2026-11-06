"""Everything that talks to git works on the work item's target repository (S58-02, ADR 0124).

The project of the scene holds its primary, `billing-api` on github.com, and a second repository,
`diamctl`, on another host with its own default branch, branch prefix and test command: what a run
clones, the branch and pull request, the guarantees that read a diff or the checks, the run's network
and the SCM installation must all come from the one the work item names.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture, recording_history, refine_result
from .kits.escalation import requests_of
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
PRIMARY_URL = "https://github.com/varga/billing-api.git"
DIAMCTL = {
    "name": "diamctl",
    "url": "https://git.acme.example/varga/diamctl.git",
    "default_branch": "trunk",
    "branch_prefix": "fix/",
    "test_command": "go test ./...",
}


async def _scene(
    setup: Fixture,
    *,
    fields: dict[str, Any] | None = None,
    documents: dict[str, Any] | None = None,
    repositories: list[dict[str, Any]] | None = None,
) -> None:
    """The project holds `repositories` beside its primary; the work item says `fields`."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        item = await session.get(WorkItem, setup.work_item_id)
        assert project is not None and item is not None
        project.config = {
            **project.config,
            "repositories": [DIAMCTL] if repositories is None else repositories,
        }
        item.fields = dict(fields or {})
        item.documents = dict(documents or {})


async def _allow_only_pypi(setup: Fixture) -> None:
    """The project's policy opens `pypi.org` alone: a repository host on the run comes from its clone."""
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, setup.project_id)
        assert row is not None
        policy = policy_model(row)
        policy.sandbox.network.allow_domains = ["pypi.org"]
        row.json_doc = policy.model_dump(mode="json", exclude_none=True)


async def _item(setup: Fixture) -> Any:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return await session.get(WorkItem, setup.work_item_id)


def _plan(setup: Fixture) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
    }


async def _prepared(setup: Fixture) -> dict[str, Any]:
    from choregos_orchestrator.activities.stage import prepare_stage

    return dict((await prepare_stage(_plan(setup)))["stage_input"])


# ───────────────────────── what a run clones ─────────────────────────


async def test_a_work_item_that_names_no_repository_works_on_the_primary(setup: Fixture) -> None:
    await _scene(setup)
    stage_input = await _prepared(setup)
    assert stage_input["repo"]["url"] == PRIMARY_URL
    assert stage_input["repo"]["base_branch"] == "main"
    assert stage_input["repo"]["work_branch"] == "choregos/123"
    assert stage_input["project"]["test_command"] == "make test"
    assert (await _item(setup)).documents["repository_fixed"] == "billing-api", "fixed at its first clone"


async def test_a_work_item_on_diamctl_clones_diamctl_with_its_branch_commands_and_host(
    setup: Fixture,
) -> None:
    await _scene(setup, fields={"repository": "diamctl"})
    await _allow_only_pypi(setup)
    stage_input = await _prepared(setup)
    assert stage_input["repo"] == {
        "url": DIAMCTL["url"],
        "base_branch": "trunk",
        "work_branch": "fix/123",
        "clone_depth": 50,
    }
    assert stage_input["project"]["test_command"] == "go test ./..."
    assert stage_input["project"]["lint_command"] == "", "diamctl's commands, not the primary's"
    reachable = stage_input["permissions"]["allow_domains"]
    assert "git.acme.example" in reachable, "the target's host is implied on the run's network"
    assert "github.com" not in reachable, "the primary's host is not: the run never clones it"
    assert (await _item(setup)).documents["repository_fixed"] == "diamctl"


async def test_a_project_with_one_repository_is_unchanged(setup: Fixture) -> None:
    await _scene(setup, repositories=[])
    await _allow_only_pypi(setup)
    stage_input = await _prepared(setup)
    assert stage_input["repo"]["url"] == PRIMARY_URL
    assert stage_input["repo"]["work_branch"] == "choregos/123"
    assert "github.com" in stage_input["permissions"]["allow_domains"]
    assert "repository_fixed" not in ((await _item(setup)).documents or {}), "nothing to choose, nothing kept"


async def test_an_unknown_repository_refuses_the_stage_before_anything_runs(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _scene(setup, fields={"repository": "diamclt"})
    with pytest.raises(ApplicationError) as refused:
        await prepare_stage(_plan(setup))
    assert refused.value.type == "RepositoryRefused"
    assert refused.value.non_retryable
    assert "`diamclt` is not a repository" in str(refused.value)
    assert "`billing-api`, `diamctl`" in str(refused.value), "the names it may take"
    assert await _runs(setup) == 0
    assert setup.adapters.executor.started == [], "no runner pod"


async def test_a_work_item_fixed_to_diamctl_refuses_another_repository(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _scene(setup, fields={"repository": "billing-api"}, documents={"repository_fixed": "diamctl"})
    with pytest.raises(ApplicationError) as refused:
        await prepare_stage(_plan(setup))
    assert refused.value.type == "RepositoryRefused"
    assert "works on `diamctl` since its first step" in str(refused.value)


async def test_a_job_step_clones_the_target_too(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _scene(setup, fields={"repository": "diamctl"})
    await _allow_only_pypi(setup)
    prepared = await prepare_stage({**_plan(setup), "job": {"command": ["make", "scan"]}})
    stage_input = prepared["stage_input"]
    assert stage_input["repo"]["url"] == DIAMCTL["url"]
    assert stage_input["repo"]["work_branch"] == "fix/123"
    assert stage_input["project"]["test_command"] == "go test ./..."
    assert "git.acme.example" in stage_input["permissions"]["allow_domains"]
    assert "github.com" not in stage_input["permissions"]["allow_domains"]


# ───────────────────────── branch, pull request, guarantees ─────────────────────────


async def test_the_branch_the_pull_request_and_the_guarantees_are_diamctl_s(setup: Fixture) -> None:
    from choregos_orchestrator.activities.gates import check_scope_violations, evaluate_gates
    from choregos_orchestrator.activities.scm import ensure_branch, open_pull_request

    await _scene(setup, fields={"repository": "diamctl"})
    ids = {"project_id": setup.project_id, "work_item_id": setup.work_item_id}
    scm = setup.adapters.scm

    assert await ensure_branch(ids) == {"branch": "fix/123"}
    assert scm.branches["varga/diamctl"] == {"fix/123": "trunk"}
    assert "varga/billing-api" not in scm.branches
    assert (await _item(setup)).documents["repository_fixed"] == "diamctl"

    await open_pull_request(ids)
    (pr,) = scm.prs.values()
    assert (pr.ref.repo, pr.ref.head, pr.ref.base) == ("varga/diamctl", "fix/123", "trunk")

    # The diff guarantees read diamctl's branch: a file outside the item's scope there is seen.
    scm.set_diff("varga/diamctl", "trunk", "fix/123", [("cmd/root.go", 3, 1)])
    [scope] = await evaluate_gates({**ids, "gates": [{"name": "scope_respected", "params": {}}]})
    assert scope["passed"] is False and "cmd/root.go" in scope["annotations"], scope
    assert await check_scope_violations(ids) == ["cmd/root.go"]

    # `ci_green` reads diamctl's pull request.
    scm.set_checks(pr.ref, "success")
    [ci] = await evaluate_gates({**ids, "gates": [{"name": "ci_green", "params": {}}]})
    assert ci["passed"] is True and not ci["pending"], ci

    assert scm.check_runs == [] or all(c["repo"] == "varga/diamctl" for c in scm.check_runs)


async def test_the_diff_shown_for_a_run_is_the_target_s(setup: Fixture) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.scm import collect_run_artifacts

    from .conftest import stage_result

    await _scene(setup, fields={"repository": "diamctl"})
    setup.adapters.scm.set_diff("varga/diamctl", "trunk", "fix/123", [("cmd/root.go", 3, 1)])
    async with session_scope() as session:
        session.add(
            Run(
                id="run-diff",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                result=stage_result().model_dump(mode="json", by_alias=True),
            )
        )
    ids = {"project_id": setup.project_id, "work_item_id": setup.work_item_id, "run_id": "run-diff"}
    assert await collect_run_artifacts(ids) == {"files": 1}
    async with session_scope() as session:
        run = await session.get(Run, "run-diff")
        assert run is not None
        assert run.result["artifacts"]["reports"]["base"] == "trunk"


# ───────────────────────── a step names the repository ─────────────────────────


async def _run_with_outputs(setup: Fixture, run_id: str, outputs: dict[str, Any], declared: list[str]) -> Any:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    from .conftest import stage_result

    result = stage_result(outputs=outputs)
    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="triage",
                status="running",
                stage_input={"transition": {"outputs": declared}},
            )
        )
    return result.model_dump(mode="json", by_alias=True)


async def test_a_declared_repository_output_becomes_the_target(setup: Fixture) -> None:
    from choregos_orchestrator.activities.outcome import record_run_outcome

    await _scene(setup)
    result = await _run_with_outputs(setup, "run-triage", {"repository": "diamctl"}, ["repository"])
    await record_run_outcome({"project_id": setup.project_id, "run_id": "run-triage", "result": result})
    assert (await _item(setup)).fields["repository"] == "diamctl"
    assert (await _prepared(setup))["repo"]["url"] == DIAMCTL["url"], "the next stage clones it"


async def test_an_undeclared_repository_output_changes_nothing(setup: Fixture) -> None:
    from choregos_orchestrator.activities.outcome import record_run_outcome

    await _scene(setup)
    result = await _run_with_outputs(setup, "run-free", {"repository": "diamctl"}, ["summary_markdown"])
    await record_run_outcome({"project_id": setup.project_id, "run_id": "run-free", "result": result})
    assert "repository" not in ((await _item(setup)).fields or {}), "an agent's word is not a field"


async def test_a_decision_question_named_repository_answers_the_target(setup: Fixture) -> None:
    from choregos_contracts import DecisionActor
    from choregos_orchestrator.activities.decision import decide, decision_request

    from .test_decision_step import _catalogue, _Transition

    await _scene(setup)
    await _catalogue(setup)
    where = DecisionActor.model_validate(
        {
            "type": "decision",
            "role": "locate",
            "state": ["title"],
            "questions": {
                "repository": {
                    "type": "choice",
                    "instructions": "Which repository holds the finding?",
                    "options": ["billing-api", "diamctl"],
                }
            },
        }
    )
    setup.adapters.decisions.answer(repository=("diamctl", 0.93))
    await decide(
        decision_request(
            project_id=setup.project_id,
            work_item_id=setup.work_item_id,
            transition=_Transition(),
            decision=where,
            attempt=1,
            last_outputs=None,
        )
    )
    assert (await _item(setup)).fields["repository"] == "diamctl"


def test_only_a_declared_repository_output_is_adopted() -> None:
    from choregos_orchestrator.activities.repositories import adopted

    assert adopted({"repository": "diamctl", "repository_confidence": 0.9}, ["repository"]) == "diamctl"
    assert adopted({"repository": " "}, ["repository"]) is None
    assert adopted({"repository": 3}, ["repository"]) is None
    assert adopted({"repository": "diamctl"}, ["severity"]) is None


# ───────────────────────── the App token of another owner ─────────────────────────


#: The other kinds a project's bundle builds, answered by a probe so nothing real is reached.
KINDS = ("tracker", "ci", "cd", "runtime", "memory", "gateway", "notify")


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """What the GitHub SCM is built with, once fakes are off (as in production)."""
    from choregos_adapters import ConnectorTypeSpec, register
    from choregos_adapters.registry import _REGISTRY, _SPECS

    received: list[dict[str, Any]] = []
    spec = ConnectorTypeSpec("probe", KINDS, {"type": "object", "properties": {}})
    for kind in KINDS:
        register(kind, "probe", spec)(lambda cfg: object())
    monkeypatch.setitem(_REGISTRY, ("scm", "github"), lambda cfg: received.append(cfg) or object())
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    try:
        yield received
    finally:
        for kind in KINDS:
            _REGISTRY.pop((kind, "probe"), None)
            _SPECS.pop((kind, "probe"), None)


async def test_a_target_of_another_owner_gets_that_owner_s_installation(
    setup: Fixture, built: list[dict[str, Any]]
) -> None:
    from choregos_api.db.models import Connector, GitHubInstallation, Project
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import (
        adapters_override,
        load_project,
        scm_for,
        set_adapters_override,
    )

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        session.add(
            Connector(project_id=project.id, kind="scm", type="github", config={"repo": "varga/billing-api"})
        )
        for kind in KINDS:
            session.add(Connector(project_id=project.id, kind=kind, type="probe", config={}))
        for installation, owner in ((55, "varga"), (77, "acme-tools")):
            session.add(
                GitHubInstallation(
                    id=installation,
                    account_login=owner,
                    account_key=owner,
                    account_id=installation * 10,
                    account_type="Organization",
                    org_id=project.org_id,
                )
            )
    shared = adapters_override()
    set_adapters_override(None)
    try:
        async with session_scope(orgs="*") as session:
            bundle = await load_project(session, setup.project_id)
            assert built[-1]["installation_id"] == 55, "the project's adapter: its connector's owner"
            same_owner = await scm_for(session, bundle, "https://github.com/varga/diamctl.git")
            assert same_owner is bundle.adapters.scm, "one owner, one installation: the same adapter"
            count = len(built)
            await scm_for(session, bundle, "https://github.com/acme-tools/diamctl.git")
        assert len(built) == count + 1
        assert built[-1]["repo"] == "acme-tools/diamctl"
        assert built[-1]["installation_id"] == 77, "the installation claimed on the target's owner"
    finally:
        set_adapters_override(shared)


# ───────────────────────── the interpreter escalates, then runs on the target ─────────────────────────


async def test_an_unknown_repository_escalates_then_the_stage_runs_on_the_repository_set(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from sqlalchemy import select
    from temporalio.worker import Replayer

    await _scene(setup, fields={"repository": "diamclt"})  # a label typed in the tracker
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the work item waits for a person", lambda s: s["pending_request"])
        assert await _runs(setup) == 0
        asked = [d for d in await requests_of(setup) if (d.payload or {}).get("reason") == "configuration"]
        assert asked and "`diamclt` is not a repository" in asked[0].payload["question"], asked

        async with session_scope() as session:  # a person sets the field right, then replays
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.fields = {"repository": "diamctl"}
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        async with session_scope() as session:
            run = (
                await session.execute(select(Run).where(Run.work_item_id == setup.work_item_id))
            ).scalar_one()
            assert run.stage_input["repo"]["url"] == DIAMCTL["url"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-repository-refused-S58-02.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
