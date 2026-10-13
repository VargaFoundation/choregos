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
    assert "missing «Escalate VIP outage»" in probleme["errors"][0]["msg"]
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

    essai = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={
            "project": "billing-api",
            "spec": {**_spec("Escalate {{ ticket.title }}.", [VIP]), "budget": {"run_usd": 3.0}},
            "input": {"title": "VIP outage", "body": "Checkout is down."},
            "budget_usd": 2.0,
        },
    )
    assert essai.status_code == 201, essai.text
    corps = essai.json()
    assert corps["budget_usd"] == pytest.approx(0.5), "a trial never spends more than 0.50 $"
    assert corps["run_id"] == f"{corps['work_item_id']}-trial-1"
    assert corps["evals"] == [{"name": "vip", "passed": True, "missing": [], "forbidden": [], "error": None}]
    demarre = get_temporal().started[f"trial-{corps['work_item_id']}"]  # type: ignore[attr-defined]
    assert demarre["budget_usd"] == pytest.approx(0.5)
    assert demarre["spec"]["instructions"] == "Escalate {{ ticket.title }}."
    assert demarre["project_id"] == project["id"]
    # The trial's work item is off the board, and readable as such.
    liste = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()["items"]
    assert corps["work_item_id"] not in {w["id"] for w in liste}
    essais = (
        await client.get(f"/api/v1/projects/{project['id']}/work-items", params={"state": "trial"})
    ).json()
    assert [w["id"] for w in essais["items"]] == [corps["work_item_id"]]


async def test_a_trial_s_draft_must_render(client: AsyncClient, project: dict[str, Any]) -> None:
    refuse = await client.post(
        "/api/v1/orgs/varga/agent-trials",
        json={"project": "billing-api", "spec": _spec("{{ ''.__class__ }}"), "input": {"title": "x"}},
    )
    assert refuse.status_code == 422, refuse.text
