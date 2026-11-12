"""A run that ended stays ended (S65-01, ADR 0143).

A run token cannot be revoked before it expires, and a run is over for the platform before it is
over for its process: the cancel failed, the pod is slow to die, a queued pod starts late, a
partition heals. The run's API only asked "is there a result?", and the orchestrator stopped
waiting without writing one. So a process that outlived its run could post a result — the run came
back as `succeeded`, and the work item took its pull request, size, risk, scope and outputs while
the next attempt was running — call the run's tools, take a git token, read its input and start.

The row now says the run is closed, and every route reads it the same way.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .kits.closed_runs import (
    ALLOWED_PATHS,
    DOCUMENTS,
    LATE_RESULT,
    PR_URL,
    a_run,
    close,
    kept,
    token_of,
)

RUNS = "/api/v1/internal/runs"

#: Every route that writes, mints or serves a run's material: (method, route, path, JSON body).
REFUSED: list[tuple[str, str, str, Any]] = [
    ("GET", "/input", "/input", None),
    ("POST", "/egress-token", "/egress-token", None),
    ("GET", "/network", "/network", None),
    (
        "POST",
        "/findings",
        "/findings",
        {"title": "N+1 on the lines", "type": "perf", "severity": "medium", "evidence": "repo.py:88"},
    ),
    (
        "POST",
        "/scope-change",
        "/scope-change",
        {"paths": ["src/shared/util.py"], "justification": "a shared function"},
    ),
    ("POST", "/question", "/question", {"text": "Which currency for credit notes?", "options": []}),
    ("GET", "/context", "/context", None),
    ("GET", "/skills", "/skills", None),
    ("GET", "/inputs", "/inputs", None),
    ("GET", "/inputs/{name}", "/inputs/spec", None),
    ("PUT", "/outputs/{name}", "/outputs/report?type=markdown", None),
    ("GET", "/knowledge", "/knowledge", None),
    ("POST", "/knowledge/search", "/knowledge/search", {"query": "credit notes"}),
    ("POST", "/memory/search", "/memory/search", {"query": "credit notes"}),
    ("GET", "/ticket", "/ticket", None),
    ("POST", "/git-token", "/git-token", None),
    ("GET", "/ci-logs", "/ci-logs", None),
    ("GET", "/tools", "/tools", None),
    ("POST", "/tools/{name}", "/tools/recherche_profils", {"metier": "accountant"}),
]

#: The routes a closed run still gets an answer from: two append-only journals, and the result's
#: own idempotent answer.
STILL_ANSWERS = {
    ("POST", "/internal/runs/{id}/events"),
    ("POST", "/internal/runs/{id}/network/decisions"),
    ("POST", "/internal/runs/{id}/result"),
}

#: The gateway's callback (S64-08b, ADR 0141 §4): signed with the gateway's master key for one run, never
#: with the run's token, so a process that outlived its run cannot call them. A call the gateway saw
#: before the run's key was closed is still counted, numbers only, as a journal is.
GATEWAY_CALLBACK = {
    ("GET", "/internal/runs/{id}/context-parts"),
    ("POST", "/internal/runs/{id}/context-breakdown"),
}

EVENTS = {"events": [{"seq": 1, "type": "session/update", "payload": {"text": "still here"}}]}
DECISIONS = {
    "decisions": [
        {
            "layer": "proxy",
            "host": "pypi.org",
            "port": 443,
            "decision": "allowed",
            "reason": "allowed:pypi.org",
        }
    ]
}


async def _call(client: AsyncClient, run_id: str, method: str, path: str, body: Any) -> Any:
    headers = token_of(run_id)
    if method == "PUT":
        return await client.put(
            f"{RUNS}/{run_id}{path}", headers={**headers, "X-Choregos-Digest": "sha256:0"}, content=b"late"
        )
    return await client.request(method, f"{RUNS}/{run_id}{path}", headers=headers, json=body)


def _is_run_closed(response: Any) -> bool:
    if response.status_code != 409:
        return False
    problem = response.json()
    assert response.headers["content-type"].startswith("application/problem+json")
    assert problem["title"] == "Run closed" and problem["code"] == "run_closed", problem
    assert [error["code"] for error in problem["errors"]] == ["run_closed"]
    return True


# ───────────────────────── a result posted late ─────────────────────────


class _StillRunning:
    """An executor whose pod never ends, and that cannot be told to stop."""

    capabilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.cancels = 0

    async def status(self, ref: Any) -> Any:
        from choregos_core import ExecStatus

        return ExecStatus(state="running")

    async def cancel(self, ref: Any) -> None:
        self.cancels += 1
        raise RuntimeError("the cluster does not answer")

    async def logs(self, ref: Any) -> Any:
        if False:
            yield ""


@pytest.fixture
def executor() -> Iterator[_StillRunning]:
    """The orchestrator's activities on fakes, with an executor that keeps the pod alive."""
    from choregos_adapters import AdapterSet
    from choregos_orchestrator.activities.base import set_adapters_override
    from choregos_orchestrator.config import reset_settings_cache

    adapters = AdapterSet.fakes()
    adapters.executor = _StillRunning()  # type: ignore[assignment]
    reset_settings_cache()
    set_adapters_override(adapters)
    yield adapters.executor  # type: ignore[misc]
    set_adapters_override(None)
    reset_settings_cache()


async def test_a_result_posted_after_the_orchestrator_stopped_waiting_changes_nothing(
    client: AsyncClient, project: dict[str, Any], executor: _StillRunning
) -> None:
    """The whole defect, end to end: the budget is spent, the cancel fails, the pod posts its result."""
    from choregos_orchestrator.activities.execution import await_run

    run_id = await a_run(project)
    outcome = await await_run(
        {"project_id": project["id"], "run_id": run_id, "timeout_minutes": 0.0005, "poll_seconds": 0.01}
    )
    assert outcome["status"] == "timed_out" and executor.cancels == 1, "the pod outlives its run"
    before = await kept(run_id)
    assert before.status == "failed" and before.result == outcome["result"]
    assert (before.result or {})["reason"] == "timeout" and before.ended_at is not None

    late = await client.post(f"{RUNS}/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT)

    assert late.status_code == 200, late.text
    assert late.json() == {"status": "already_recorded", "run_id": run_id}, "answered as a replay"
    after = await kept(run_id)
    assert after == before, "neither the run nor its work item moved, and nothing was recorded"
    assert after.status == "failed", "the run did not come back to life as succeeded"
    assert (after.pr_url, after.size, after.risk) == (PR_URL, "M", "low")
    assert after.allowed_paths == ALLOWED_PATHS and after.documents == DOCUMENTS
    assert (after.finished_events, after.artifacts, after.receipts) == (0, 0, 0)


@pytest.mark.parametrize(
    ("status", "reason"), [("failed", "timeout"), ("failed", "lost"), ("cancelled", "cancelled")]
)
async def test_a_late_result_is_a_replay_however_the_run_was_closed(
    client: AsyncClient, project: dict[str, Any], status: str, reason: str
) -> None:
    run_id = await a_run(project)
    await close(run_id, status=status, reason=reason)
    before = await kept(run_id)

    late = await client.post(f"{RUNS}/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT)

    assert late.json() == {"status": "already_recorded", "run_id": run_id}
    assert await kept(run_id) == before
    assert (before.status, (before.result or {})["reason"]) == (status, reason)


@pytest.mark.parametrize("status", ["failed", "cancelled", "timed_out", "succeeded"])
async def test_a_run_closed_without_a_result_does_not_take_one_either(
    client: AsyncClient, project: dict[str, Any], status: str
) -> None:
    """A row abandoned or cancelled before closing wrote a result: a terminal status, nothing else."""
    run_id = await a_run(project, status=status)
    before = await kept(run_id)

    late = await client.post(f"{RUNS}/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT)

    assert late.json() == {"status": "already_recorded", "run_id": run_id}
    assert await kept(run_id) == before and before.result is None


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_the_result_of_an_open_run_is_recorded_as_before(
    client: AsyncClient, project: dict[str, Any], status: str
) -> None:
    run_id = await a_run(project, status=status)

    posted = await client.post(f"{RUNS}/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT)

    assert posted.json() == {"status": "recorded", "run_id": run_id}
    after = await kept(run_id)
    assert (after.status, after.size, after.risk) == ("succeeded", "XL", "high")
    assert after.pr_url == LATE_RESULT["artifacts"]["pr_url"] and after.finished_events == 1
    again = await client.post(f"{RUNS}/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT)
    assert again.json()["status"] == "already_recorded" and await kept(run_id) == after


# ───────────────────────── every other route ─────────────────────────


@pytest.mark.parametrize(("method", "route", "path", "body"), REFUSED, ids=[r[1] for r in REFUSED])
async def test_a_closed_run_is_refused_with_one_typed_error(
    client: AsyncClient, project: dict[str, Any], method: str, route: str, path: str, body: Any
) -> None:
    run_id = await a_run(project)
    opened = await _call(client, run_id, method, path, body)
    assert not _is_run_closed(opened), f"an open run is not refused as closed: {opened.text}"
    # A second run, so that what the open one just did is not what `kept` compares.
    run_id = await a_run(project)
    await close(run_id)
    before = await kept(run_id)

    refused = await _call(client, run_id, method, path, body)

    assert _is_run_closed(refused), f"{method} {route}: {refused.status_code} {refused.text}"
    assert await kept(run_id) == before, "a refused call writes nothing"


@pytest.mark.parametrize("closing", ["by the orchestrator", "by its own result", "before results were kept"])
async def test_the_refusal_is_the_same_however_the_run_was_closed(
    client: AsyncClient, project: dict[str, Any], closing: str
) -> None:
    if closing == "by the orchestrator":
        run_id = await a_run(project)
        await close(run_id)
    elif closing == "by its own result":
        run_id = await a_run(project, status="succeeded", result={"status": "done", "summary": "done"})
    else:
        run_id = await a_run(project, status="cancelled")
    for method, _route, path, body in REFUSED:
        assert _is_run_closed(await _call(client, run_id, method, path, body)), f"{closing}: {method} {path}"


@pytest.fixture
def github_app(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A GitHub App set up, its token minting intercepted: what it was asked for is kept."""
    from choregos_adapters.github import GitHubAppAuth, InstallationToken

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "5242171")
    monkeypatch.setenv(
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    asked: list[str] = []

    async def mint(self: Any, client: Any, repo: str, **options: Any) -> InstallationToken:
        asked.append(repo)
        return InstallationToken(token="ghs_of_the_run", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", mint)
    return asked


async def test_no_git_token_is_minted_for_a_closed_run(
    client: AsyncClient, project: dict[str, Any], github_app: list[str]
) -> None:
    run_id = await a_run(project)
    opened = await client.post(f"{RUNS}/{run_id}/git-token", headers=token_of(run_id))
    assert opened.status_code == 200 and opened.json()["token"] == "ghs_of_the_run"
    assert github_app == ["acme/billing-api"]

    await close(run_id, status="cancelled", reason="cancelled")
    refused = await client.post(f"{RUNS}/{run_id}/git-token", headers=token_of(run_id))

    assert _is_run_closed(refused)
    assert github_app == ["acme/billing-api"], "the provider was not asked for a second token"


async def test_the_journals_of_a_closed_run_still_accept(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """What a process did after its run ended is what an operator wants to read."""
    from choregos_api.db.models import NetworkDecision, RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id = await a_run(project)
    await close(run_id)
    before = await kept(run_id)

    events = await client.post(f"{RUNS}/{run_id}/events", headers=token_of(run_id), json=EVENTS)
    decisions = await client.post(
        f"{RUNS}/{run_id}/network/decisions", headers=token_of(run_id), json=DECISIONS
    )

    assert (events.status_code, events.json()) == (202, {"accepted": 1}), events.text
    assert (decisions.status_code, decisions.json()) == (202, {"accepted": 1}), decisions.text
    async with session_scope(orgs="*") as session:
        journal = (await session.execute(select(RunEvent).where(RunEvent.run_id == run_id))).scalars().all()
        reported = (
            (await session.execute(select(NetworkDecision).where(NetworkDecision.run_id == run_id)))
            .scalars()
            .all()
        )
    assert [(e.seq, e.type) for e in journal] == [(1, "session/update")]
    assert [(d.host, d.decision) for d in reported] == [("pypi.org", "allowed")]
    assert await kept(run_id) == before, "a journal entry closes nothing and reopens nothing"


async def test_a_caller_without_the_runs_token_learns_nothing_of_its_state(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Authentication comes before the refusal: 401 and 403 as for an open run, never 409."""
    closed, other = await a_run(project), await a_run(project)
    await close(closed)
    for method, _route, path, body in REFUSED:
        anonymous = await client.request(method, f"{RUNS}/{closed}{path}", json=body)
        assert anonymous.status_code == 401, f"{method} {path}: {anonymous.status_code}"
        stranger = await client.request(method, f"{RUNS}/{closed}{path}", headers=token_of(other), json=body)
        assert stranger.status_code == 403, f"{method} {path}: {stranger.status_code}"


# ───────────────────────── what is not closed ─────────────────────────


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_a_queued_or_running_run_is_served(
    client: AsyncClient, project: dict[str, Any], status: str
) -> None:
    run_id = await a_run(project, status=status)
    fetched = await client.get(f"{RUNS}/{run_id}/input", headers=token_of(run_id))
    assert fetched.status_code == 200 and fetched.json()["run_id"] == run_id
    assert (await client.post(f"{RUNS}/{run_id}/egress-token", headers=token_of(run_id))).status_code == 200
    assert (await client.get(f"{RUNS}/{run_id}/context", headers=token_of(run_id))).status_code == 200


async def test_a_run_resumed_after_a_persons_answer_is_open_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A run has no `suspended` status: it waits for a person holding the result that asked, closed
    while its sandbox sleeps, and `resume_run` is what opens it again. The row is then the one
    `resume_run` leaves (`test_run_closed_when_the_wait_ends.py`, orchestrator suite, plays it)."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    asked = {"status": "needs_human", "summary": "which currency?", "reason": "question"}
    run_id = await a_run(project, status="failed", result=asked)
    assert _is_run_closed(await client.get(f"{RUNS}/{run_id}/input", headers=token_of(run_id)))

    async with session_scope(orgs="*") as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.result, run.status, run.ended_at = None, "running", None
        run.tokens = {"resumed": 1, "resumed_for": "answer-1"}

    fetched = await client.get(f"{RUNS}/{run_id}/input", headers=token_of(run_id))
    assert fetched.status_code == 200, "the resumed runner reads its input again"
    posted = await client.post(f"{RUNS}/{run_id}/result", headers=token_of(run_id), json=LATE_RESULT)
    assert posted.json()["status"] == "recorded", "and its result is its own"


# ───────────────────────── nothing forgotten ─────────────────────────


def _dependencies(route: Any) -> set[Any]:
    found, stack = set(), [route.dependant]
    while stack:
        dependant = stack.pop()
        found.add(dependant.call)
        stack.extend(dependant.dependencies)
    return found


def test_every_route_of_the_run_api_is_closed_with_its_run_or_is_a_journal() -> None:
    """A route added to the run's API takes the open-run token, or is named here as a journal: the
    guard is one dependency, and no route is left to remember it."""
    from choregos_api.deps import open_network_claims, open_run_claims
    from choregos_api.routers.internal import router

    guards = {open_run_claims, open_network_claims}
    guarded, unguarded = set(), set()
    for route in router.routes:
        path = str(getattr(route, "path", ""))
        if not path.startswith("/internal/runs/{id}"):
            continue
        for method in route.methods - {"HEAD"}:  # type: ignore[attr-defined]
            (guarded if guards & _dependencies(route) else unguarded).add((method, path))
    assert unguarded == STILL_ANSWERS | GATEWAY_CALLBACK
    assert guarded == {(method, f"/internal/runs/{{id}}{route}") for method, route, _, _ in REFUSED}, (
        "a guarded route this file does not call: add it to REFUSED"
    )
