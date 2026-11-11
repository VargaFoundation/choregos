"""A tool call is attributed to the door it came through, and the door never authorises (S57-06).

ADR 0122 §4: the door — `model` (the agent's MCP client), `script` (a script, through the runner's
tool bridge), `job` (a job step, ADR 0056) — and the script's digest land on the call's ledger line,
its `choregos.tool.called` event and its `execute_tool` span. A job step's calls are `job` whatever
they send: the API reads it from the run. A run that is not a job step cannot claim `job` (400). The
cost reports group by it (`group_by=origin`).

ADR 0122 §2: governance does not depend on the door. Every refusal of `callRunTool` is the same for
every door, and the route reads the door for attribution only (a structural test below).
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_core import telemetry
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from .kits.tool_catalogue import intercept, run_with_catalogue
from .test_broker import ORG, TOOLS, _prepare

DIGEST = "5c" * 32
SCRIPT = {"X-Choregos-Tool-Origin": "script", "X-Choregos-Script": DIGEST}
#: A job step's command: `prepare_job` writes it in the run's StageInput, beside `backend: job`.
JOB_COMMAND = ["python", "snapshot.py"]


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    telemetry.configure_tracing("choregos-api", exporter)
    yield exporter
    telemetry.shutdown_tracing()


@pytest.fixture
def supplier(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(tools=[dict(o) for o in TOOLS], token="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield server


async def _another_run(template_id: str, run_id: str, *, job: dict[str, Any] | None) -> dict[str, str]:
    """A run on the template's work item: of the template's agent, or a job step when `job` is given."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        template = await session.get(Run, template_id)
        assert template is not None
        item = await session.get(WorkItem, template.work_item_id)
        assert item is not None
        key = item.tracker_key
        session.add(
            Run(
                id=run_id,
                work_item_id=template.work_item_id,
                project_id=template.project_id,
                stage_role=template.stage_role,
                transition_id=template.transition_id,
                status="running",
                **(
                    {"backend": "job", "stage_input": {"job": job}}
                    if job is not None
                    else {"agent_slug": template.agent_slug, "agent_version": template.agent_version}
                ),
            )
        )
    token = mint_run_token(run_id, project_slug="billing-api", work_item_key=key, ttl_minutes=30)
    return {"Authorization": f"Bearer {token}"}


async def _booked(run_id: str) -> list[Any]:
    """The run's tool lines, the provisional ones of S57-03 left out."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core.tool_ledger import IN_FLIGHT
    from sqlalchemy import select

    async with session_scope() as session:
        query = select(CostLedger).where(
            CostLedger.run_id == run_id, CostLedger.kind == "tool", CostLedger.provider != IN_FLIGHT
        )
        return list((await session.execute(query)).scalars())


async def _events(run_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(Event).where(Event.type == "choregos.tool.called"))).scalars()
        return [dict(e.payload) for e in rows if e.subject == run_id]


def _provider_answers(monkeypatch: pytest.MonkeyPatch, seen: list[Any]) -> None:
    intercept(
        monkeypatch,
        httpx.Response(
            200, json={"profiles": []}, request=httpx.Request("GET", "https://api.annuaire.example")
        ),
        seen,
    )


# ───────────────────────── one call per door ─────────────────────────


@pytest.mark.parametrize(
    ("job", "sent", "origin", "digest"),
    [
        (False, {}, "model", None),
        (False, {"X-Choregos-Tool-Origin": "model"}, "model", None),
        (False, SCRIPT, "script", DIGEST),
        # A job step sends nothing today (the runner gives its command the run token): it is `job`.
        (True, {}, "job", None),
        (True, {"X-Choregos-Tool-Origin": "job"}, "job", None),
        # Whatever it says: the door is read from the run, and a job step has no bridge to journal a script.
        (True, SCRIPT, "job", None),
        (True, {"X-Choregos-Tool-Origin": "model"}, "job", None),
    ],
    ids=["model-by-default", "model", "script", "job", "job-says-job", "job-says-script", "job-says-model"],
)
async def test_a_call_is_booked_with_its_door_on_the_ledger_the_event_and_the_span(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
    job: bool,
    sent: dict[str, str],
    origin: str,
    digest: str | None,
) -> None:
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-door-1")
    if job:
        run_id = "run-door-job-1"
        headers = await _another_run("run-door-1", run_id, job={"command": JOB_COMMAND, "tools": {}})
    _provider_answers(monkeypatch, [])

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers={**headers, **sent},
        json={"metier": "data engineer"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status_code"] == 200
    (line,) = await _booked(run_id)
    assert (line.origin, line.script_sha256) == (origin, digest)
    (event,) = await _events(run_id)
    assert event["origin"] == origin and event.get("script_sha256") == digest
    (span,) = spans.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["choregos.tool.origin"] == origin
    assert attributes.get("choregos.tool.script_sha256") == digest


async def test_a_run_that_is_not_a_job_step_cannot_claim_to_be_one(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    """The API knows the claim false: booking it would make the ledger lie, so it is refused as an
    origin outside the contract is, before anything is called or counted."""
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-door-2")
    seen: list[Any] = []
    _provider_answers(monkeypatch, seen)

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers={**headers, "X-Choregos-Tool-Origin": "job"},
        json={"metier": "x"},
    )

    assert response.status_code == 400, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    (error,) = response.json()["errors"]
    assert error["code"] == "tool_origin_refused" and error["loc"] == ["header", "X-Choregos-Tool-Origin"]
    assert "not a job step" in error["msg"]
    assert seen == [], "the provider is never called"
    assert await _booked(run_id) == [] and await _events(run_id) == []
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert "choregos.tool.origin" not in (span.attributes or {}), "no door recorded for a false claim"


async def test_a_refused_call_is_a_span_with_its_door_too(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-door-5")

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/not_in_the_catalogue", headers={**headers, **SCRIPT}, json={}
    )

    assert response.status_code == 404
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    attributes = dict(span.attributes or {})
    assert (attributes["choregos.tool.origin"], attributes["choregos.tool.script_sha256"]) == (
        "script",
        DIGEST,
    )


async def test_a_run_named_job_without_the_job_spec_is_not_a_job_step(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job step is what `prepare_job` writes: `backend: job` AND the job's spec in the StageInput."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-door-3")
    async with session_scope() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.backend = "job"
    _provider_answers(monkeypatch, [])

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )

    assert response.status_code == 200, response.text
    (line,) = await _booked(run_id)
    assert line.origin == "model"


# ───────────────────────── the cost reports, by door ─────────────────────────


async def test_the_three_doors_are_three_groups_of_the_cost_reports(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real calls, one per door, then the reports: what the route booked is what `group_by=origin`
    reads, at project and organisation scope, in JSON and CSV; a place still in flight is no call."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from choregos_core.tool_ledger import IN_FLIGHT

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-door-4")
    job_headers = await _another_run(run_id, "run-door-job-4", job={"command": JOB_COMMAND, "tools": {}})
    _provider_answers(monkeypatch, [])
    calls = [
        (run_id, headers),
        (run_id, {**headers, **SCRIPT}),
        (run_id, {**headers, **SCRIPT}),
        ("run-door-job-4", job_headers),
    ]
    for called, sent in calls:
        response = await client.post(
            f"/api/v1/internal/runs/{called}/tools/recherche_profils", headers=sent, json={"metier": "x"}
        )
        assert response.status_code == 200, response.text
    async with session_scope() as session:
        session.add_all(
            [
                # A model call says nothing of a door, nor does a tool line booked before S57-06.
                CostLedger(project_id=project["id"], run_id=run_id, ts=datetime.now(UTC), kind="model",
                           model="m", cost_usd=1.0, cost_eur=0.5),
                CostLedger(project_id=project["id"], run_id=run_id, ts=datetime.now(UTC), kind="tool",
                           provider="annuaire", model="recherche_profils", cost_eur=0.02),
                # A call still in flight holds a place under the cap (S57-03): not a call yet.
                CostLedger(project_id=project["id"], run_id=run_id, ts=datetime.now(UTC), kind="tool",
                           provider=IN_FLIGHT, model="recherche_profils"),
            ]
        )  # fmt: skip

    for url in (f"/api/v1/projects/{project['id']}/costs", "/api/v1/orgs/varga/costs"):
        report = (await client.get(url, params={"group_by": "origin"})).json()
        assert report["group_by"] == "origin"
        assert {row["key"]: row["runs"] for row in report["rows"]} == {
            "model": 1,
            "script": 2,
            "job": 1,
            "—": 2,
        }, url
        tools = (await client.get(url, params={"group_by": "origin", "kind": "tool"})).json()
        by_door = {row["key"]: (row["runs"], row["cost_eur"]) for row in tools["rows"]}
        assert by_door == {"model": (1, 0.02), "script": (2, 0.04), "job": (1, 0.02), "—": (1, 0.02)}, url
        csv = await client.get(f"{url}.csv", params={"group_by": "origin", "kind": "tool"})
        lines = csv.text.lstrip("﻿").splitlines()
        assert lines[0].startswith("origin;cout_usd;"), lines[0]
        assert {line.split(";")[0] for line in lines[1:5]} == {"model", "script", "job", "—"}, lines


#: Contract-first (ADR 0001): groupings the contract declares before the API accepts them, each with the
#: story that implements it. An entry the API accepts must be removed from here.
GROUPINGS_AHEAD_OF_THE_API: dict[str, str] = {"run": "S64-02"}


def test_the_contract_lists_the_groupings_the_api_accepts() -> None:
    import re

    import choregos_contracts as contracts
    from choregos_api.routers import costs

    accepted = re.fullmatch(r"\^\((.*)\)\$", costs.GROUP_PATTERN)
    assert accepted is not None
    paths = contracts.load_openapi()["paths"]
    for route in (
        "/projects/{id}/costs",
        "/projects/{id}/costs.csv",
        "/orgs/{org}/costs",
        "/orgs/{org}/costs.csv",
    ):
        (group_by,) = [p for p in paths[route]["get"]["parameters"] if p.get("name") == "group_by"]
        declared = [g for g in group_by["schema"]["enum"] if g not in GROUPINGS_AHEAD_OF_THE_API]
        assert declared == accepted.group(1).split("|"), route
    served = sorted(set(GROUPINGS_AHEAD_OF_THE_API) & set(accepted.group(1).split("|")))
    assert not served, f"accepted now, remove from GROUPINGS_AHEAD_OF_THE_API: {served}"
    assert "origin" in costs.GROUPS


# ───────────────────────── the door never authorises ─────────────────────────

_WRITING_CATALOGUE = """
tools:
  - name: create_ticket
    description: Creates a ticket.
    provider: tickets
    input_schema: { type: object, properties: { title: { type: string } } }
    http:
      method: POST
      url: https://api.tickets.example/v1/tickets
    price_eur: 0.01
"""

#: Every refusal path of `callRunTool`, then a read and the cap, in the order a run meets them.
_CALLS: list[tuple[str, dict[str, Any]]] = [
    ("fournisseur__inconnu", {}),  # unknown: 404, as a tool not allowed is
    ("create_ticket", {"title": "x"}),  # a catalogue write: 409
    ("fournisseur__suivi_commande", {}),  # an argument its schema refuses: 400
    ("fournisseur__stock", {}),  # an operation the organisation forbids: 404
    ("fournisseur__commander_poste", {}),  # under approval: proposed (202), nothing reaches the server
    ("fournisseur__suivi_commande", {"numero": "42"}),  # an allowed read: the last place under the cap
    ("fournisseur__suivi_commande", {"numero": "43"}),  # the cap (2): 429
]
#: What each call answers, whatever the door: the status, then the problem's title and its error
#: codes, or the tool's status, what a proposal says and the places left under the cap.
_EXPECTED: list[tuple[Any, ...]] = [
    (404, "Tool not found", ()),
    (409, "Catalogue write refused", ("catalogue_write_refused",)),
    (400, "Arguments refused", ()),
    (404, "Tool not found", ()),
    (200, 202, "pending_approval", 1),
    (200, 200, None, 0),
    (429, "Tool call cap reached", ()),
]


async def _cap(project_id: str, cap: int) -> None:
    """The project's policy caps a run at `cap` tool calls (`budgets.tool_calls_per_run`)."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        latest = (
            select(PolicyDef).where(PolicyDef.project_id == project_id).order_by(PolicyDef.version.desc())
        )
        policy = (await session.execute(latest)).scalars().first()
        assert policy is not None
        document = dict(policy.json_doc)
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": cap}
        policy.json_doc = document


def _outcome(response: httpx.Response) -> tuple[Any, ...]:
    body = response.json()
    if response.status_code != 200:
        return (response.status_code, body["title"], tuple(e.get("code") for e in body.get("errors") or []))
    result = body["result"] if isinstance(body["result"], dict) else {}
    return (200, body["status_code"], result.get("status"), body["remaining"])


def _detail(response: httpx.Response) -> Any:
    """What a call's caller reads beyond its outcome, less what names its own run or its own action."""
    body = response.json()
    if response.status_code != 200:
        return body.get("detail")
    result = body["result"] if isinstance(body["result"], dict) else {}
    return sorted(body), result.get("message"), result.get("structuredContent")


async def test_every_refusal_is_the_same_for_every_door(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    supplier: Any,
) -> None:
    """ADR 0122 §2 and invariant 7 of the plan: no authorisation decision reads the origin. Six runs,
    three doors (`model` by default and said, `script` with its digest; `job` from job steps that
    say nothing, `job`, or `script`): each meets the same calls and learns the same thing."""
    from choregos_api import catalogue as catalogue_service
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    base, _headers = await _prepare(client, project, ["*"])
    template = base.split("/")[-2]
    forbidden = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/stock", json={"policy": "forbidden"}
    )
    assert forbidden.status_code == 200, forbidden.text
    catalogue = tmp_path / "catalogue.yaml"
    catalogue.write_text(_WRITING_CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(catalogue))
    catalogue_service.clear_cache()
    async with session_scope() as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        row.config = {**(row.config or {}), "tools": ["create_ticket"]}
    await _cap(project["id"], 2)

    job = {"command": JOB_COMMAND, "tools": {"fournisseur": ["*"]}}
    arms: list[tuple[str, dict[str, str], str]] = [
        ("run-parity-model", {}, "model"),
        ("run-parity-model-said", {"X-Choregos-Tool-Origin": "model"}, "model"),
        ("run-parity-script", SCRIPT, "script"),
        ("run-parity-job", {}, "job"),
        ("run-parity-job-said", {"X-Choregos-Tool-Origin": "job"}, "job"),
        ("run-parity-job-script", SCRIPT, "job"),
    ]
    outcomes: dict[str, list[tuple[Any, ...]]] = {}
    details: dict[str, list[Any]] = {}
    for run_id, sent, door in arms:
        run_headers = await _another_run(template, run_id, job=job if door == "job" else None)
        answers = [
            await client.post(
                f"/api/v1/internal/runs/{run_id}/tools/{name}", headers={**run_headers, **sent}, json=body
            )
            for name, body in _CALLS
        ]
        outcomes[run_id] = [_outcome(answer) for answer in answers]
        details[run_id] = [_detail(answer) for answer in answers]

    assert outcomes["run-parity-model"] == _EXPECTED, (
        "the baseline is the route's refusals, not a vacuous match"
    )
    for run_id, _sent, _door in arms:
        assert outcomes[run_id] == _EXPECTED, f"{run_id}: the door changed what the call may do"
        assert details[run_id] == details["run-parity-model"], f"{run_id}: the door changed what it reads"
    for run_id, _sent, door in arms:
        assert {line.origin for line in await _booked(run_id)} == {door}, "attributed all the same"
    assert [name for name, _ in supplier.calls] == ["suivi_commande"] * len(arms), "one read per run"


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> Iterator[ast.AST]:
    while node in parents:
        node = parents[node]
        yield node


def test_the_route_reads_the_door_for_attribution_only() -> None:
    """Structural: in `call_tool`, the origin and the digest are read by the header check, the job
    rule, the span, the ledger line and the event — never in a condition, never by the policy, the
    cap, the broker or a provider. A rule keyed on the door (ADR 0122 §2) would have to read one."""
    from choregos_api.routers import internal

    function = ast.parse(textwrap.dedent(inspect.getsource(internal.call_tool))).body[0]
    assert isinstance(function, ast.AsyncFunctionDef)
    door = {"tool_origin", "script", "origin", "script_sha256"}
    allowed = {"_call_origin", "_attributed", "telemetry.record_tool_origin", "CostLedger", "persist_event"}
    parents = {child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)}
    readers: set[str] = set()
    for node in ast.walk(function):
        if not (isinstance(node, ast.Name) and node.id in door and isinstance(node.ctx, ast.Load)):
            continue
        enclosing = [a for a in _ancestors(node, parents) if isinstance(a, ast.Call)]
        assert enclosing, f"`{node.id}` is read outside a call"
        readers.add(ast.unparse(enclosing[0].func))
        for ancestor in _ancestors(node, parents):
            if isinstance(ancestor, ast.If | ast.While | ast.Assert | ast.Match):
                test = ancestor.subject if isinstance(ancestor, ast.Match) else ancestor.test
                assert node not in set(ast.walk(test)), f"`{node.id}` decides: {ast.unparse(test)}"
    assert readers == allowed, f"the door is read by {sorted(readers - allowed)}, or no longer recorded"
    # Bound by the header check, then by the job rule: nothing else rewrites what the door says.
    bound = sorted(
        target.id
        for node in ast.walk(function)
        if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign | ast.NamedExpr)
        for target in ast.walk(node.targets[0] if isinstance(node, ast.Assign) else node.target)
        if isinstance(target, ast.Name) and target.id in door
    )
    assert bound == ["origin", "origin", "script_sha256", "script_sha256"]


def test_nothing_else_in_the_api_reads_the_door() -> None:
    """The headers are read by the tool route alone, and the ledger's door by the cost reports alone."""
    import choregos_api

    package = Path(choregos_api.__file__).parent
    header_readers: set[str] = set()
    column_readers: set[str] = set()
    for path in sorted(package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(package).as_posix()
        if any(
            name in text for name in ("TOOL_ORIGIN_HEADER", "SCRIPT_HEADER", "_call_origin", "_attributed")
        ):
            header_readers.add(relative)
        if "CostLedger.origin" in text or "CostLedger.script_sha256" in text:
            column_readers.add(relative)
    assert header_readers == {"routers/internal.py"}
    assert column_readers == {"routers/costs.py"}
