"""Try a draft agent version, and its eval suite as a publish gate (S32-03).

A failing eval blocks publishing (409, the failing cases named). The published version's cases
gate the next version too, so a failing case cannot be deleted to get through. A trial runs the
draft once on a sample input, under a budget capped at 0.50 $, on a work item the board never lists.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

AGENTS = "/api/v1/orgs/varga/agents"
VIP = {"name": "vip", "input": {"title": "VIP outage"}, "must_contain": ["Escalate VIP outage"]}


def _spec(instructions: str, evals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"instructions": instructions, "evals": evals or []}


async def test_a_failing_eval_blocks_publishing(client: AsyncClient, admin: str) -> None:
    cree = await client.post(
        AGENTS,
        json={
            "slug": "triage",
            "display_name": "Triage",
            "spec": _spec("Escalate {{ ticket.title }}.", [VIP]),
        },
    )
    assert cree.status_code == 201, cree.text
    # The next version drops the case and breaks it: the published suite still gates it.
    refuse = await client.post(f"{AGENTS}/triage/versions", json=_spec("Handle {{ ticket.title }} calmly."))
    assert refuse.status_code == 409, refuse.text
    probleme = refuse.json()
    assert [e["loc"] for e in probleme["errors"]] == [["evals", "vip"]]
    assert "missing “Escalate VIP outage”" in probleme["errors"][0]["msg"]
    versions = (await client.get(f"{AGENTS}/triage")).json()["latest_version"]
    assert versions == 1, "the refused version is not published"
    passe = await client.post(f"{AGENTS}/triage/versions", json=_spec("Escalate {{ ticket.title }} now."))
    assert passe.status_code == 201, passe.text


async def test_an_agent_is_not_created_failing_its_own_suite(client: AsyncClient, admin: str) -> None:
    refuse = await client.post(
        AGENTS,
        json={"slug": "triage", "display_name": "Triage", "spec": _spec("Sort {{ ticket.title }}.", [VIP])},
    )
    assert refuse.status_code == 409, refuse.text
    assert (await client.get(f"{AGENTS}/triage")).status_code == 404


async def test_a_trial_runs_the_draft_capped_and_off_the_board(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import get_temporal

    trial = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={
            "project": "billing-api",
            "spec": {**_spec("Escalate {{ ticket.title }}.", [VIP]), "budget": {"run_usd": 3.0}},
            "input": {"title": "VIP outage", "body": "Checkout is down."},
            "budget_usd": 2.0,
        },
    )
    assert trial.status_code == 201, trial.text
    body = trial.json()
    assert body["budget_usd"] == pytest.approx(0.5), "a trial never spends more than 0.50 $"
    assert body["run_id"] == f"{body['work_item_id']}-trial-1"
    assert body["evals"] == [{"name": "vip", "passed": True, "missing": [], "forbidden": [], "error": None}]
    started = get_temporal().started[f"trial-{body['work_item_id']}"]  # type: ignore[attr-defined]
    assert started["budget_usd"] == pytest.approx(0.5)
    assert started["spec"]["instructions"] == "Escalate {{ ticket.title }}."
    assert started["project_id"] == project["id"]
    # The trial's work item is off the board, and readable as such.
    listing = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()["items"]
    assert body["work_item_id"] not in {w["id"] for w in listing}
    trials = (
        await client.get(f"/api/v1/projects/{project['id']}/work-items", params={"state": "trial"})
    ).json()
    assert [w["id"] for w in trials["items"]] == [body["work_item_id"]]


async def test_a_trial_s_draft_must_render(client: AsyncClient, project: dict[str, Any]) -> None:
    refuse = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={"project": "billing-api", "spec": _spec("{{ ''.__class__ }}"), "input": {"title": "x"}},
    )
    assert refuse.status_code == 422, refuse.text


async def test_an_eval_case_plays_a_ticket_filed_from_a_tracker(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """S51-04: a case renders `ticket.fields` — its own, or `{}` — so instructions reading the
    requester behind a guard publish, and a case can check what they say about one."""
    instructions = (
        "{% if ticket.fields.tracker is defined %}Filed by "
        "{{ ticket.fields.tracker.reporter.display_name }}.{% else %}Filed in the console.{% endif %}"
    )
    tracker = {"kind": "jira", "key": "FON-270", "reporter": {"display_name": "Camille Martin"}}
    cases = [
        {"name": "console", "input": {"title": "x"}, "must_contain": ["Filed in the console."]},
        {"name": "jira", "input": {"title": "x", "fields": {"tracker": tracker}},
         "must_contain": ["Filed by Camille Martin."]},
    ]  # fmt: skip
    created = await client.post(
        AGENTS, json={"slug": "access", "display_name": "Access", "spec": _spec(instructions, cases)}
    )
    assert created.status_code == 201, created.text

    trial = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={"project": "billing-api", "spec": _spec(instructions), "input": cases[1]["input"]},
    )
    assert trial.status_code == 201, trial.text
    read = await client.get(f"/api/v1/work-items/{trial.json()['work_item_id']}")
    assert read.json()["fields"] == {"tracker": tracker}, "the trial plays the sample's fields"
