"""Does a stored workflow still start where it says it does?

The DSL sets a simple rule: **the first declared state is the initial state** (section 1.4), and
`Workflow.initial_state` read it as `next(iter(self.states))`, the order of the dictionary.

PostgreSQL `jsonb` **does not preserve the order of the keys**: it stores them by length, then by
bytes. A workflow stored in the `json` column of `workflow_defs` therefore comes out in another
order than the one it was written in, and "the first declared state" becomes "the shortest state
name". Observed on 2026-09-27 on the tenant: a work item created with a workflow whose first state
was called `inbox` and whose terminal state was `done` was born **in the terminal state**. The
interpreter saw a final state, closed the work item, and ran no step: 1.5 seconds, zero runs, no
anomaly reported anywhere.

The three shipped templates escape it **by luck**: in `default-simple`, `inbox` is both the first
declared and the shortest at an alphabetical tie. That is why nobody had seen it.

The most important test is the last one: it makes the round trip through a real database.
"""

from __future__ import annotations

from choregos_core import parse_workflow

from .conftest import without_postgres

#: A workflow whose first declared state is NOT the shortest name. `end` (3) would come
#: before `starts` (6) in the order of `jsonb`.
WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: order, version: 1 }
actors:
  agent_a: { type: agent, role: implement, model: "profile:standard" }
  human: { type: human, group: org-admins, sla_hours: 24 }
states:
  starts: { display: Starts, kind: wait }
  middle: { display: Middle }
  end: { display: End, terminal: true }
transitions:
  - id: t-do
    from: starts
    to: middle
    by: agent_a
    outputs: [report]
    gates: [outputs_present]
    on_fail: { to: starts, max_attempts: 2, escalate_to: end }
  - id: t-close
    from: middle
    to: end
    by: human
    timeout_hours: 72
"""


def test_the_yaml_says_where_to_start() -> None:
    """The reference: read from the YAML, the order is the one it was written in."""
    wf, report = parse_workflow(WORKFLOW, strict=False)
    assert report.valid, [f"{e.code}: {e.message}" for e in report.errors]
    assert wf.initial_state == "starts"


def test_a_json_round_trip_does_not_move_the_start() -> None:
    """Without a database: simulate the storage of `jsonb` (length, then bytes)."""
    wf, _ = parse_workflow(WORKFLOW, strict=False)
    document = wf.model_dump(mode="json")
    assert document["initial"] == "starts", "the parser must write the start into the document"
    document["states"] = dict(sorted(document["states"].items(), key=lambda pair: (len(pair[0]), pair[0])))
    assert next(iter(document["states"])) == "end", "the test must really shuffle the order"

    from choregos_contracts import Workflow

    reread = Workflow.model_validate(document)
    assert reread.initial_state == "starts", (
        f"the workflow thinks it starts at `{reread.initial_state}` after a JSON round trip. "
        "A work item created this way is born in its terminal state."
    )


def test_a_workflow_cannot_start_with_its_end() -> None:
    """The guard that would have turned this silence into a refusal."""
    broken = WORKFLOW.replace(
        "metadata: { name: order, version: 1 }", "metadata: { name: order, version: 1 }\ninitial: end"
    )
    _, report = parse_workflow(broken, strict=False)
    codes = [e.code for e in report.errors]
    assert "workflow.initial_state_terminal" in codes, f"expected a refusal, got {codes}"


@without_postgres
async def test_through_a_real_database_the_start_survives(pg_app: object) -> None:
    """The test that matters: `jsonb` stores the keys, and the start must hold anyway."""
    from choregos_api.db.models import Organization, Project, WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_api.services.definitions import workflow_model
    from choregos_contracts import Workflow
    from sqlalchemy import select

    wf, _ = parse_workflow(WORKFLOW, strict=False)
    async with session_scope(orgs="*") as session:
        org = Organization(slug="order", name="Order")
        session.add(org)
        await session.flush()
        project = Project(
            org_id=org.id,
            slug="order",
            name="Order",
            status="active",
            config={"slug": "order", "org": "order"},
        )
        session.add(project)
        await session.flush()
        row = WorkflowDef(
            project_id=project.id,
            name="order",
            version=1,
            yaml=WORKFLOW,
            json_doc=wf.model_dump(mode="json"),
            checksum="trial",
            is_active=True,
        )
        session.add(row)
        await session.flush()
        identifier = row.id

    async with session_scope(orgs="*") as session:
        stored = (await session.execute(select(WorkflowDef).where(WorkflowDef.id == identifier))).scalar_one()
        order_of_keys = list(stored.json_doc["states"])
        assert order_of_keys[0] == "end", f"jsonb should have moved the keys, order obtained: {order_of_keys}"
        assert Workflow.model_validate(stored.json_doc).initial_state == "starts"
        assert workflow_model(stored).initial_state == "starts", (
            "this is the path the API and the orchestrator take"
        )
