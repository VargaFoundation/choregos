"""Live evaluation of an agent revision (S40-02d, ADR 0071): capped trials, scored from their runs.

Nothing here needs a model: the trials start on `FakeTemporal`, and the runs they would write are written
by the test. "Not run" is never "passed".
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, ORG, as_user
from .test_agent_release_channels import agent_of, release, save

pytestmark = pytest.mark.usefixtures("admin")


def live(name: str, contains: list[str], *, cost: float | None = None, **extra: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "name": name,
        "live": True,
        "input": {"title": name, "body": "please"},
        "must_contain": contains,
        **extra,
    }
    if cost is not None:
        case["max_cost_usd"] = cost
    return case


async def agent_with(
    client: AsyncClient, cases: list[dict[str, Any]], policy: dict[str, Any] | None = None
) -> None:
    response = await client.post(
        f"{ORG}/agents",
        json={
            "slug": "reviewer",
            "display_name": "Reviewer",
            "spec": {"instructions": "Review the change.", "evals": cases},
            "release_policy": policy,
        },
    )
    assert response.status_code == 201, response.text


async def evaluate(client: AsyncClient, project: str, version: int = 1) -> Any:
    return await client.post(
        f"{ORG}/agents/reviewer/versions/{version}/evaluations", json={"project": project}
    )


async def write_runs(work_items: list[str], outcomes: list[tuple[str, str, str]]) -> None:
    """The trial runs: `(run status, result status, summary)` for each trial work item, in order."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        for item_id, (status, result_status, summary) in zip(work_items, outcomes, strict=True):
            item = (await session.execute(select(WorkItem).where(WorkItem.id == item_id))).scalar_one()
            session.add(
                Run(
                    id=f"{item_id}-trial-1",
                    work_item_id=item.id,
                    project_id=item.project_id,
                    stage_role="implement",
                    status=status,
                    cost_usd=0.1,
                    result={"status": result_status, "summary": summary},
                )
            )


def items_of(evaluation: dict[str, Any]) -> list[str]:
    return [case["run_id"].removesuffix("-trial-1") for case in evaluation["cases"]]


async def test_starting_an_evaluation_starts_one_capped_trial_per_live_case(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    cases = [live(f"case {n}", ["ok"], cost=0.4) for n in range(3)]
    await agent_with(client, [*cases, {"name": "level one", "must_contain": ["Review"]}])
    started = await evaluate(client, project["slug"])
    assert started.status_code == 201, started.text
    body = started.json()
    assert (body["status"], body["score"], body["project"], body["version"]) == (
        "running",
        None,
        "billing-api",
        1,
    )
    assert len(body["cases"]) == 3 and all(c["status"] == "running" for c in body["cases"])
    assert body["budget_usd"] == pytest.approx(1.2)
    workflows = get_temporal().started  # type: ignore[attr-defined]
    payloads = [workflows[f"trial-{item}"] for item in items_of(body)]
    assert all(p["budget_usd"] <= 0.5 for p in payloads) and sum(p["budget_usd"] for p in payloads) <= 2.0
    assert all(p["spec"]["instructions"] == "Review the change." for p in payloads)


async def test_an_evaluation_that_could_spend_more_than_the_policy_allows_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Red without the budget check: five cases at 0.50 $ is 2.50 $, above the 2.00 $ default."""
    from choregos_api.temporal import get_temporal

    await agent_with(client, [live(f"case {n}", ["ok"], cost=0.5) for n in range(5)])
    refused = await evaluate(client, project["slug"])
    assert refused.status_code == 409 and "2.50 $" in refused.text
    assert not [k for k in get_temporal().started if k.startswith("trial-")]  # type: ignore[attr-defined]
    raised = {"on_publish": "stable", "max_eval_usd": 3.0}
    assert (await client.put(f"{ORG}/agents/reviewer/release-policy", json=raised)).status_code == 200
    assert (await evaluate(client, project["slug"])).status_code == 201


async def test_a_revision_without_live_cases_or_with_too_many_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await agent_with(client, [{"name": "level one", "must_contain": ["Review"]}])
    none = await evaluate(client, project["slug"])
    assert none.status_code == 409 and "no live case" in none.text
    many = await client.post(
        f"{ORG}/agents/reviewer/versions",
        json={"instructions": "Review it.", "evals": [live(f"c{n}", ["x"], cost=0.01) for n in range(11)]},
    )
    assert many.status_code == 201
    assert (await evaluate(client, project["slug"], 2)).status_code == 409
    assert (await evaluate(client, project["slug"], 9)).status_code == 404


async def test_the_score_is_read_from_the_trial_runs(client: AsyncClient, project: dict[str, Any]) -> None:
    """Two of three pass: score 2/3, `failed` under a 0.9 minimum (red if a failing case passed)."""
    policy = {"on_publish": "candidate", "stable": {"min_live_score": 0.9}}
    await agent_with(client, [live(f"case {n}", ["approved"], cost=0.1) for n in range(3)], policy)
    started = (await evaluate(client, project["slug"])).json()
    mid = (await client.get(f"{ORG}/agents/reviewer/versions/1/evaluations")).json()[0]
    assert mid["status"] == "running" and mid["score"] is None, "no run yet: not passed"
    await write_runs(
        items_of(started),
        [
            ("succeeded", "done", "approved: all good"),
            ("succeeded", "done", "approved, with notes"),
            ("succeeded", "done", "rejected: missing tests"),
        ],
    )
    (done,) = (await client.get(f"{ORG}/agents/reviewer/versions/1/evaluations")).json()
    assert done["status"] == "failed" and done["score"] == pytest.approx(0.6667, abs=1e-3)
    assert [c["status"] for c in done["cases"]] == ["passed", "passed", "failed"]
    assert "missing “approved”" in done["cases"][2]["reason"]
    assert done["cases"][0]["cost_usd"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("case_extra", "run", "passes"),
    [
        ({}, ("succeeded", "done", "ok fine"), True),
        ({}, ("failed", "failed", "ok fine"), False),
        ({}, ("succeeded", "blocked", "ok fine"), False),
        ({"expect_status": "blocked"}, ("succeeded", "blocked", "ok: I need a person"), True),
        ({"must_not_contain": ["secret"]}, ("succeeded", "done", "ok but a secret"), False),
    ],
)
async def test_what_a_case_judges(
    client: AsyncClient,
    project: dict[str, Any],
    case_extra: dict[str, Any],
    run: tuple[str, str, str],
    passes: bool,
) -> None:
    await agent_with(client, [live("only", ["ok"], cost=0.1, **case_extra)])
    started = (await evaluate(client, project["slug"])).json()
    await write_runs(items_of(started), [run])
    (done,) = (await client.get(f"{ORG}/agents/reviewer/versions/1/evaluations")).json()
    assert done["status"] == ("passed" if passes else "failed")


async def test_without_a_model_to_call_the_evaluation_is_not_run_never_passed(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import config
    from choregos_api.services import agent_live_evals
    from choregos_api.temporal import get_temporal

    real = config.get_settings()
    quiet = real.model_copy(update={"fakes": False, "gateway_master_key": ""})
    monkeypatch.setattr(agent_live_evals, "get_settings", lambda: quiet)
    await agent_with(
        client,
        [live("only", ["ok"], cost=0.1)],
        {"on_publish": "candidate", "stable": {"min_live_score": 0.5}},
    )
    body = (await evaluate(client, project["slug"])).json()
    assert body["status"] == "not_run" and body["score"] is None
    assert body["cases"][0]["status"] == "not_run" and "no gateway key" in body["cases"][0]["reason"]
    assert not [k for k in get_temporal().started if k.startswith("trial-")]  # type: ignore[attr-defined]
    check = next(
        c
        for c in (await client.get(f"{ORG}/agents/reviewer/versions/1/readiness")).json()["checks"]
        if c["name"] == "live_eval"
    )
    assert check["passed"] is False and "could not run" in check["detail"]


async def test_the_live_score_gates_testing_and_production(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    policy = {
        "on_publish": "none",
        "stable": {"min_live_score": 0.5},
        "candidate": {"min_live_score": 0.5},
    }
    await agent_with(client, [live("only", ["ok"], cost=0.1)], policy)
    await save_v2(client)
    no_eval = await release(client, "reviewer", channel="candidate", version=2)
    assert no_eval.status_code == 409 and no_eval.json()["errors"][0]["loc"] == ["checks", "live_eval"]
    started = (await evaluate(client, project["slug"], 2)).json()
    await write_runs(items_of(started), [("succeeded", "done", "nope")])
    failing = await release(client, "reviewer", channel="candidate", version=2)
    assert failing.status_code == 409 and "score 0%" in failing.text
    second = (await evaluate(client, project["slug"], 2)).json()
    await write_runs(items_of(second), [("succeeded", "done", "ok done")])
    sent = await release(client, "reviewer", channel="candidate", version=2)
    assert sent.status_code == 201, sent.text
    promoted = await release(client, "reviewer", channel="stable", version=2)
    assert promoted.status_code == 201, "the newest finished evaluation is the one that counts"
    back = await release(client, "reviewer", channel="stable", version=1)
    assert back.status_code == 201 and back.json()["kind"] == "rollback", "a rollback skips the live gate"
    assert (await agent_of(client))["stable_version"] == 1


async def save_v2(client: AsyncClient) -> None:
    await save(client, "reviewer", 2, evals=[live("only", ["ok"], cost=0.1)])


async def test_the_level_one_gate_ignores_live_cases(client: AsyncClient) -> None:
    """Red if `evaluate()` judged a live case on the rendered instructions: it asserts on a run's output."""
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agent_evals import evaluate as level_one

    spec = AgentSpec(instructions="Review.", evals=[live("needs a run", ["never in the instructions"])])
    assert level_one(spec, spec.evals) == []
    created = await client.post(
        f"{ORG}/agents", json={"slug": "plain", "display_name": "Plain", "spec": spec.model_dump(mode="json")}
    )
    assert created.status_code == 201, created.text


async def test_trying_the_agent_is_the_right_an_evaluation_needs(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from .agent_scopes_kit import add_member

    await agent_with(client, [live("only", ["ok"], cost=0.1)])
    await add_member(client, "viewer@varga.dev", "viewer")
    await as_user(client, "viewer@varga.dev")
    refused = await evaluate(client, project["slug"])
    assert refused.status_code == 403
    await as_user(client, ADMIN)
    assert (await evaluate(client, "nobody")).status_code in {403, 404}
