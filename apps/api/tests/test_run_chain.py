"""The run's chain and the step's partition (S40-22, ADR 0077 points 2 and 4).

- A work item records who started it, `<kind>:<subject>`, from the door it came through.
- `GET /runs/{id}/access` shows `initiator → run A → run B`, each run with its agent version, from
  what the orchestrator recorded at stage start; a run from before S40-22 shows no chain.
- A step reads an earlier step's artefact only when its transition declares it as an input.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes import FakeObjectStore
from httpx import AsyncClient

DECK = b"PK\x03\x04" + b"slides" * 50
SPEC = "# Spec\n\nDeduct credit notes from the total.\n"


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from choregos_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


async def _work_item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Quarterly review", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _run(
    item: dict[str, Any],
    project: dict[str, Any],
    run_id: str,
    role: str,
    *,
    inputs: list[str] | None = None,
    inputs_from: list[str] | None = None,
    initiated_by: str | None = None,
    agent: tuple[str, int] | None = None,
) -> dict[str, str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=item["id"],
                project_id=project["id"],
                stage_role=role,
                status="running",
                agent_slug=agent[0] if agent else None,
                agent_version=agent[1] if agent else None,
                stage_input={"transition": {"outputs": [], "inputs": inputs or []}},
                initiated_by=initiated_by,
                inputs_from=inputs_from,
            )
        )
    token = mint_run_token(
        run_id, project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    return {"Authorization": f"Bearer {token}"}


def _access_schema() -> dict[str, Any]:
    import choregos_contracts as contracts

    route = contracts.load_openapi()["paths"]["/runs/{id}/access"]["get"]
    return dict(route["responses"]["200"]["content"]["application/json"]["schema"])


# ───────────────────────────── who started it ─────────────────────────────


async def test_a_work_item_created_in_the_console_names_the_person(
    client: AsyncClient, project: dict[str, Any], admin: str
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    item = await _work_item(client, project)
    async with session_scope() as session:
        born = await session.get(WorkItem, item["id"])
        assert born is not None and born.initiated_by == f"person:{admin}"


def test_the_initiator_names_its_door_and_refuses_an_unknown_kind() -> None:
    from choregos_api.rbac import SYSTEM, Principal
    from choregos_api.services.initiator import initiator, of_principal

    ada = Principal(user_id="u1", email="ada@example.org")
    assert of_principal(ada, "api") == "person:ada@example.org"
    assert of_principal(ada, "mcp") == "mcp:ada@example.org"
    assert of_principal(ada, "a2a") == "a2a:ada@example.org"
    assert of_principal(SYSTEM, "schedule") == "system", "a system principal is never a person"
    assert initiator("a2a", "rovo") == "a2a:rovo", "the inbound A2A door (S40-23) has its kind"
    assert initiator("eval") == "eval"
    with pytest.raises(ValueError, match="unknown initiator kind"):
        initiator("agent", "planner")


# ───────────────────────────── the chain ─────────────────────────────


async def test_the_access_record_shows_the_initiator_and_each_run_with_its_agent_version(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from jsonschema import Draft202012Validator

    item = await _work_item(client, project)
    who = "person:ada@example.org"
    await _run(item, project, "T-1-plan-1", "plan", inputs_from=[], initiated_by=who, agent=("planner", 3))
    await _run(item, project, "T-1-implement-1", "implement", inputs_from=["T-1-plan-1"], initiated_by=who)
    await _run(
        item,
        project,
        "T-1-review-1",
        "review",
        inputs_from=["T-1-implement-1", "T-1-plan-1"],
        initiated_by=who,
        agent=("reviewer", 7),
    )

    answer = await client.get("/api/v1/runs/T-1-review-1/access")
    assert answer.status_code == 200, answer.text
    record = answer.json()
    assert not list(Draft202012Validator(_access_schema()).iter_errors(record)), "the contract holds"
    assert record["chain"] == {
        "initiated_by": who,
        "runs": [
            {
                "run_id": "T-1-plan-1",
                "stage": "plan",
                "agent_slug": "planner",
                "agent_version": 3,
                "inputs_from": [],
            },
            {
                "run_id": "T-1-implement-1",
                "stage": "implement",
                "agent_slug": None,
                "agent_version": None,
                "inputs_from": ["T-1-plan-1"],
            },
            {
                "run_id": "T-1-review-1",
                "stage": "review",
                "agent_slug": "reviewer",
                "agent_version": 7,
                "inputs_from": ["T-1-implement-1", "T-1-plan-1"],
            },
        ],
    }


async def test_a_run_recorded_before_the_chain_shows_none_and_a_foreign_run_is_not_walked(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item = await _work_item(client, project)
    other = await _work_item(client, project)
    await _run(item, project, "T-1-old-1", "plan")
    await _run(other, project, "T-2-plan-1", "plan", inputs_from=[], agent=("planner", 1))
    await _run(item, project, "T-1-review-1", "review", inputs_from=["T-2-plan-1"], initiated_by="eval")

    old = (await client.get("/api/v1/runs/T-1-old-1/access")).json()
    assert "chain" not in old, "a run from before S40-22 says nothing rather than guess"
    chain = (await client.get("/api/v1/runs/T-1-review-1/access")).json()["chain"]
    assert [r["run_id"] for r in chain["runs"]] == ["T-1-review-1"], "the walk stays in the work item"


# ───────────────────────────── the step's partition ─────────────────────────────


async def test_a_step_cannot_read_an_artefact_it_did_not_declare_as_an_input(
    client: AsyncClient, project: dict[str, Any], store: FakeObjectStore
) -> None:
    from choregos_api.db.models import HandOffReceipt, Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.artifacts import record_inline_outputs, store_file
    from sqlalchemy import select

    item = await _work_item(client, project)
    await _run(item, project, "T-1-write-1", "write", agent=("presenter", 2))
    async with session_scope() as session:
        work_item = await session.get(WorkItem, item["id"])
        writer = await session.get(Run, "T-1-write-1")
        owner = await session.get(Project, project["id"])
        assert work_item is not None and writer is not None and owner is not None
        await store_file(
            session,
            store,
            org_id=owner.org_id,
            item=work_item,
            run=writer,
            name="deck",
            type_="pptx",
            data=DECK,
        )
        await record_inline_outputs(
            session, org_id=owner.org_id, item=work_item, run=writer, outputs={"spec_markdown": SPEC}
        )
    headers = await _run(item, project, "T-1-review-1", "review", inputs=["spec_markdown"])

    listed = await client.get("/api/v1/internal/runs/T-1-review-1/inputs", headers=headers)
    assert [e["name"] for e in listed.json()] == ["spec_markdown"], "only the declared input is listed"
    declared = await client.get("/api/v1/internal/runs/T-1-review-1/inputs/spec_markdown", headers=headers)
    assert declared.status_code == 200 and declared.text == SPEC
    for name in ("deck", "spec_markdown@1", "../deck"):
        undeclared = await client.get(f"/api/v1/internal/runs/T-1-review-1/inputs/{name}", headers=headers)
        assert undeclared.status_code == 404, (name, undeclared.status_code)
        assert DECK not in undeclared.content

    async with session_scope() as session:
        reads = (
            await session.execute(
                select(HandOffReceipt.output).where(
                    HandOffReceipt.run_id == "T-1-review-1", HandOffReceipt.kind == "read"
                )
            )
        ).scalars()
        assert set(reads) == {"spec_markdown"}, "an undeclared artefact leaves no read, it was not served"
