"""The passage receipts between stages (S25-04).

One stage produces a spec; the next reads it. Nothing said WHICH revision it had read: an output
rewritten in the meantime went through without a trace. Each stored output leaves a "produced"
receipt under its digest, each read a "read" receipt under the digest of what was read, and the
public route says what the output is worth today.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

SPEC = "# Spec\n\nDeduct credit notes from the total.\n"
SPEC_REVISED = "# Spec\n\nDeduct credit notes, then round half to even.\n"


async def _work_item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Credit notes", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(
    ticket: dict[str, Any],
    project: dict[str, Any],
    run_id: str,
    role: str,
    inputs: list[str] | None = None,
) -> dict[str, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=ticket["id"],
                project_id=project["id"],
                stage_role=role,
                status="running",
                stage_input={"transition": {"inputs": inputs or []}},
            )
        )
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=ticket["tracker_key"], ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


async def _produce(client: AsyncClient, run_id: str, headers: dict[str, str], spec: str) -> None:
    result = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "spec written",
        "outputs": {"spec_markdown": spec},
    }
    posted = await client.post(f"/api/v1/internal/runs/{run_id}/result", headers=headers, json=result)
    assert posted.status_code == 200, posted.text


async def _hand_offs(client: AsyncClient, ticket: dict[str, Any]) -> dict[str, Any]:
    read = await client.get(f"/api/v1/work-items/{ticket['id']}/hand-offs")
    assert read.status_code == 200, read.text
    return {passage["output"]: passage for passage in read.json()}


async def test_the_next_stage_reads_the_exact_revision_produced(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _work_item(client, project)
    refiner = await _run(ticket, project, "run-refiner", "refine")
    await _produce(client, "run-refiner", refiner, SPEC)

    implementer = await _run(ticket, project, "run-implement", "implement", ["spec_markdown"])
    read_back = await client.get("/api/v1/internal/runs/run-implement/ticket", headers=implementer)
    assert read_back.json()["spec_markdown"] == SPEC
    # Reading twice makes one receipt; what does not exist (the plan) makes none.
    await client.get("/api/v1/internal/runs/run-implement/ticket", headers=implementer)

    spec = (await _hand_offs(client, ticket))["spec_markdown"]
    assert [(e["kind"], e["run_id"], e["stage"]) for e in spec["events"]] == [
        ("produced", "run-refiner", "refine"),
        ("read", "run-implement", "implement"),
    ]
    produced, read_event = spec["events"]
    assert produced["digest"].startswith("sha256:") and len(produced["digest"]) == 71
    assert read_event["digest"] == produced["digest"], "the revision read is the revision produced"
    assert spec["current_digest"] == produced["digest"]


async def test_an_output_modified_afterwards_changes_digest(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Rewritten by hand after its receipt, the spec no longer has the digest of its last "produced" receipt,
    and the stage that reads it next reads a revision no stage produced."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    ticket = await _work_item(client, project)
    refiner = await _run(ticket, project, "run-r1", "refine")
    await _produce(client, "run-r1", refiner, SPEC)
    async with session_scope() as session:
        item = await session.get(WorkItem, ticket["id"])
        assert item is not None
        item.documents = {**(item.documents or {}), "spec_markdown": SPEC_REVISED}

    implementer = await _run(ticket, project, "run-i1", "implement", ["spec_markdown"])
    await client.get("/api/v1/internal/runs/run-i1/ticket", headers=implementer)

    spec = (await _hand_offs(client, ticket))["spec_markdown"]
    produced, read_event = spec["events"]
    assert spec["current_digest"] != produced["digest"], "modified afterwards: it shows"
    assert read_event["digest"] == spec["current_digest"] != produced["digest"]


async def test_a_receipt_no_longer_changes_a_new_revision_makes_another(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _work_item(client, project)
    first = await _run(ticket, project, "run-a1", "refine")
    await _produce(client, "run-a1", first, SPEC)
    # The same result posted again: already recorded, no second receipt.
    await _produce(client, "run-a1", first, SPEC)
    retry = await _run(ticket, project, "run-a2", "refine")
    await _produce(client, "run-a2", retry, SPEC_REVISED)

    spec = (await _hand_offs(client, ticket))["spec_markdown"]
    assert [(e["kind"], e["run_id"]) for e in spec["events"]] == [
        ("produced", "run-a1"),
        ("produced", "run-a2"),
    ]
    first_event, second_event = spec["events"]
    assert first_event["digest"] != second_event["digest"]
    assert spec["current_digest"] == second_event["digest"]


async def test_the_receipts_of_a_work_item_read_with_the_right_to_read_the_project(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    ticket = await _work_item(client, project)
    assert await _hand_offs(client, ticket) == {}
    missing = await client.get("/api/v1/work-items/nope/hand-offs")
    assert missing.status_code == 404


def test_the_digest_does_not_depend_on_key_order() -> None:
    from choregos_api.services.receipts import digest, read_by_the_prompt

    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})
    assert digest("x") != digest("x ")
    # The prompt embeds the spec only if it cites it; a declared input is read, present or not empty.
    documents = {"spec_markdown": SPEC, "plan_markdown": "# Plan", "profiles": ["a", "b"], "empty": ""}
    assert read_by_the_prompt(documents, ["profiles", "empty"], f"Read this:\n{SPEC}") == {
        "profiles": ["a", "b"],
        "spec_markdown": SPEC,
    }


async def test_a_step_cannot_read_the_spec_or_plan_it_did_not_declare(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """ADR 0077 section 4 (finding #890): `/ticket` follows the same partition as `/inputs/{name}`.
    A step that declares nothing gets neither the spec nor the plan, and leaves no read receipt."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    ticket = await _work_item(client, project)
    refiner = await _run(ticket, project, "run-refiner", "refine")
    await _produce(client, "run-refiner", refiner, SPEC)
    async with session_scope() as session:
        item = await session.get(WorkItem, ticket["id"])
        assert item is not None
        item.documents = {**(item.documents or {}), "plan_markdown": "1. do it"}

    nothing = await _run(ticket, project, "run-nothing", "review")
    read_back = (await client.get("/api/v1/internal/runs/run-nothing/ticket", headers=nothing)).json()
    assert read_back["spec_markdown"] is None and read_back["plan_markdown"] is None
    assert read_back["title"] == "Credit notes", "the ticket itself is still served"

    only_the_plan = await _run(ticket, project, "run-plan", "review", ["plan_markdown"])
    read_back = (await client.get("/api/v1/internal/runs/run-plan/ticket", headers=only_the_plan)).json()
    assert read_back["spec_markdown"] is None and read_back["plan_markdown"] == "1. do it"

    hand_offs = await _hand_offs(client, ticket)
    reads = {
        (passage_name, e["run_id"])
        for passage_name, p in hand_offs.items()
        for e in p["events"]
        if e["kind"] == "read"
    }
    assert reads == {("plan_markdown", "run-plan")}, "only what was served leaves a read receipt"
