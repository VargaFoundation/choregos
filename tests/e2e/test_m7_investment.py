"""M7 — an investment research campaign end to end (S38-09), on fakes, with the real runner.

A work item is a campaign: a universe of symbols, a period, an out-of-sample start and thresholds.
The three job steps (ADR 0056) run the **real** runner in job mode, which runs the real
`choregos-quant` command in a subprocess; the snapshot job reads the bars through the platform —
`POST /internal/runs/{id}/tools/marketdata__get_bars` with its run's token — served by a real HTTP
server, and the platform calls the scripted `FakeMarketData`. The two agent steps are scripted:
the strategist returns strategies, the analyst a memo built from the measured metrics.

Campaign A has a trending product: after the verdict the campaign opens one child work item for it
(S38-15, ADR 0057, workflow `product-memo`) — the memo, then a person adopts it — and, the child
settled, the campaign's owner decides. Campaign C has two trending products: two children, each
decided on its own, and the campaign passes only once both are settled. Campaign B has only random
walks: the verdict, on measured facts, ends it in `not_relevant`. An analyst who quotes a number the
backtest did not compute for its product is refused until the child goes to review.

Sources that disagree beyond the campaign's `max_diverging_symbols` end in `to_review` (S38-14).

Remove a brick — the job mode, the measured marking, `numbers_measured`, the market data route, the
`spawn:` of the campaign — and a scenario turns red.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.fakes.executor import FakeExecutor
from choregos_contracts import StageInput, StageResult, StageStatus
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec, utcnow
from httpx import AsyncClient

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ORG = "/api/v1/orgs/varga"
KEY = "free-market-data-key"
ATTESTATION = (
    "I read the product memos and the backtest report; this is an internal research decision, not advice"
)
STRATEGIES = {
    "strategies": [
        {"id": "s1", "hypothesis": "trend", "family": "sma_cross", "params": {"fast": 10, "slow": 50}},
        {"id": "s2", "hypothesis": "ema", "family": "ema_trend", "params": {"span": 40}},
        {"id": "s3", "hypothesis": "momentum", "family": "momentum", "params": {"lookback": 60, "skip": 5}},
    ]
}
PERIOD = {"start": "2018-01-01", "oos_start": "2022-01-01", "end": "2024-12-31"}
HISTORIES = Path(__file__).resolve().parents[1] / "replay" / "histories"


async def _replayed(handle: Any, archive: str) -> None:
    """The campaign's history replays against the current code, and is archived for the next ones
    (`CHOREGOS_ARCHIVER_HISTORIQUE=1`): a change to the interpreter that would decide differently
    on a job step fails `tests/replay`."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / archive
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


def sawtooth(
    start: str = "2018-01-01", end: str = "2024-12-31", seed: int = 7, phase: int = 0
) -> list[dict[str, Any]]:
    """A product with long trends, up then down every 60 business days: a trend rule finds it."""
    rng = random.Random(seed)
    price, bars, day, i = 100.0, [], date.fromisoformat(start), 0
    while day <= date.fromisoformat(end):
        if day.weekday() < 5:
            up = ((i + phase) // 60) % 2 == 0
            price *= math.exp((0.006 if up else -0.006) + rng.gauss(0, 0.004))
            p = round(price, 4)
            high, low = round(price * 1.005, 4), round(price * 0.995, 4)
            bars.append(
                {"date": day.isoformat(), "open": p, "high": high, "low": low, "close": p, "adj_close": p}
                | {"volume": 1e6}
            )
            i += 1
        day += timedelta(days=1)
    return bars


Agent = Callable[[StageInput], Awaitable[StageResult]]


class Bench(FakeExecutor):
    """The executor of the bench: a job step runs the real runner, an agent step is scripted.

    The runner runs in this event loop and talks to the API over real HTTP, as in a pod; its
    command runs in a subprocess. The run's result is the one the runner posted to the API."""

    def __init__(self, api_url: str, root: Path, agents: dict[str, Agent]) -> None:
        super().__init__()
        self.api_url, self.root, self.agents = api_url, root, agents
        self.jobs: dict[str, asyncio.Task[Any]] = {}
        self.outcomes: dict[str, Any] = {}

    async def start(self, spec: StageJobSpec) -> ExecRef:
        ref = ExecRef(
            kind=self.kind, name=f"bench-{spec.run_id}", namespace=spec.namespace, run_id=spec.run_id
        )
        if spec.run_id in self.statuses:
            return ref
        self.started.append(spec)
        stage_input = spec.stage_input
        assert stage_input is not None
        if stage_input.job is not None:
            self.statuses[spec.run_id] = ExecStatus(state="running", started_at=utcnow())
            self.jobs[spec.run_id] = asyncio.create_task(self._job(spec))
            return ref
        self.results[spec.run_id] = await self.agents[stage_input.transition.role](stage_input)
        self.statuses[spec.run_id] = ExecStatus(
            state="succeeded", exit_code=0, started_at=utcnow(), ended_at=utcnow()
        )
        return ref

    async def _job(self, spec: StageJobSpec) -> None:
        from choregos_runner.config import RunnerSettings
        from choregos_runner.runner import Runner

        settings = RunnerSettings(
            run_id=spec.run_id,
            api_url=spec.api_url,
            run_token=spec.run_token,
            workspace=self.root / spec.run_id,
        )
        try:
            outcome = await Runner(settings=settings).run()
            self.outcomes[spec.run_id] = outcome
            state = "succeeded" if int(outcome.exit_code) == 0 else "failed"
            message = outcome.detail
        except Exception as exc:  # the run fails on its own message, never silently
            self.outcomes[spec.run_id] = exc
            state, message = "failed", f"runner raised: {exc!r}"
        self.statuses[spec.run_id] = ExecStatus(
            state=state, exit_code=0 if state == "succeeded" else 1, message=message, ended_at=utcnow()
        )


@pytest.fixture
def market(monkeypatch: pytest.MonkeyPatch) -> Any:
    import choregos_adapters
    from choregos_adapters.fakes.marketdata import FakeMarketData

    fake = FakeMarketData()
    fake.fixtures["TRND.US"] = sawtooth()
    fake.fixtures["TRN2.US"] = sawtooth(seed=11, phase=20)
    monkeypatch.setattr(choregos_adapters, "FAUX_MARKETDATA", fake)
    monkeypatch.setenv("CONNECTOR_E2E_MARKETDATA_KEY", KEY)
    return fake


@pytest.fixture
async def api_url(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """The API on a real port: the runner, and the job's subprocess, reach it over HTTP."""
    import uvicorn
    from choregos_api.main import create_app
    from choregos_orchestrator.config import reset_settings_cache

    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    )
    serving = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_URL", url)
    monkeypatch.setenv("CHOREGOS_HEARTBEAT_SECONDS", "1")
    reset_settings_cache()
    yield url
    server.should_exit = True
    await serving
    reset_settings_cache()


async def _child_fields(stage_input: StageInput) -> dict[str, Any]:
    """The fields of the product's work item, as the platform stored them: the product's measured
    metrics, copied from the campaign's backtest when the child was opened."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        item = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == stage_input.work_item.key))
        ).scalar_one()
        assert "product" in item.fields, "the child holds what the backtest measured for its product"
        return dict(item.fields)


async def strategist(stage_input: StageInput) -> StageResult:
    return StageResult(
        status=StageStatus.DONE, summary="three hypotheses", outputs={"strategies": STRATEGIES}
    )


async def honest_analyst(stage_input: StageInput) -> StageResult:
    fields = await _child_fields(stage_input)
    product = fields["product"]
    oos = product["strategies"][product["best_in_sample"]]["oos"]
    memo = (
        f"# Memo {fields['symbol']}\n\n"
        f"- {fields['symbol']}: out-of-sample CAGR {oos['cagr']:.1%}, deflated Sharpe {oos['dsr']:.2f}, "
        f"max drawdown {abs(oos['max_drawdown']):.1%}."
    )
    return StageResult(status=StageStatus.DONE, summary="memo", outputs={"memo_markdown": memo})


async def inventive_analyst(stage_input: StageInput) -> StageResult:
    memo = "# Product memo\n\nTRND.US should return 987.6% next year."
    return StageResult(status=StageStatus.DONE, summary="memo", outputs={"memo_markdown": memo})


async def _organisation(client: AsyncClient) -> dict[str, Any]:
    connector = await client.post(
        f"{ORG}/connectors",
        json={"name": "marketdata", "kind": "marketdata", "type": "stooq",
              "secret_refs": {"api_key": "env:CONNECTOR_E2E_MARKETDATA_KEY"}},
    )  # fmt: skip
    assert connector.status_code == 201, connector.text
    created = await client.post(
        f"{ORG}/projects",
        json={"slug": "research", "name": "Research", "template_ref": "investment-research",
              "config": {"slug": "research", "org": "varga"}},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    project = dict(created.json())
    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    return project


class _Items(dict[str, Any]):
    """The fake tracker's items: a child opened by a fan-out is born in the platform, and mirrors its
    state to a tracker that has never heard of it — the fake learns it on first sight."""

    def __missing__(self, key: str) -> Any:
        from choregos_core.domain import WorkItemData

        self[key] = WorkItemData(key=key, title=key, state="Todo")
        return self[key]


async def _campaign(
    platform: Platform, project: dict[str, Any], fields: dict[str, Any]
) -> tuple[dict[str, str], Any]:
    from choregos_api.temporal import interpreter_id
    from choregos_core.domain import WorkItemData

    if not isinstance(platform.adapters.tracker.items, _Items):
        platform.adapters.tracker.items = _Items(platform.adapters.tracker.items)
    created = await platform.client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Campaign", "fields": fields}
    )
    assert created.status_code == 201 and created.json()["workflow_name"] == "campaign", created.text
    item = {"id": created.json()["id"], "key": created.json()["tracker_key"]}
    platform.adapters.tracker.items.setdefault(
        item["key"], WorkItemData(key=item["key"], title="C", state="Todo")
    )
    handle = await platform.env.client.start_workflow(
        "WorkflowInterpreter",
        {"project_id": project["id"], "project_slug": "research", "work_item_id": item["id"],
         "tracker_key": item["key"]},
        id=interpreter_id("research", item["key"]),
        task_queue="e2e",
    )  # fmt: skip
    return item, handle


async def _state(
    client: AsyncClient, item_id: str, expected: str, what: str, tries: int = 1200
) -> dict[str, Any]:
    for _ in range(tries):
        read = (await client.get(f"/api/v1/work-items/{item_id}")).json()
        if read["state"] == expected:
            return dict(read)
        await asyncio.sleep(0.1)
    raise AssertionError(f"{what}: the work item is in `{read['state']}`, not `{expected}`")


async def _request(client: AsyncClient, item_id: str) -> dict[str, Any]:
    for _ in range(600):
        read = (await client.get(f"/api/v1/work-items/{item_id}")).json()
        if read.get("pending_request"):
            return dict(read["pending_request"])
        await asyncio.sleep(0.1)
    raise AssertionError("no human request opened")


async def _artifact(client: AsyncClient, item_id: str, name: str) -> dict[str, Any]:
    listed = (await client.get(f"/api/v1/work-items/{item_id}/artifacts")).json()
    found = [a for a in listed if a["name"] == name]
    assert found, f"no artifact `{name}` in {[a['name'] for a in listed]}"
    return dict(found[-1])


async def _child(client: AsyncClient, parent_key: str, rank: int) -> dict[str, Any]:
    """The child work item of a fan-out, by its key (`<parent>.<rank>`): id, state, fields."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    for _ in range(600):
        async with session_scope() as session:
            row = (
                await session.execute(select(WorkItem).where(WorkItem.tracker_key == f"{parent_key}.{rank}"))
            ).scalar_one_or_none()
            if row is not None:
                found = {"id": row.id, "key": row.tracker_key, "fields": dict(row.fields)}
                workflow = (await client.get(f"/api/v1/work-items/{row.id}")).json()["workflow_name"]
                return found | {"workflow": workflow}
        await asyncio.sleep(0.1)
    raise AssertionError(f"no child {parent_key}.{rank} opened")


async def _children(parent_key: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            (await session.execute(select(WorkItem).where(WorkItem.tracker_key.like(f"{parent_key}.%"))))
            .scalars()
            .all()
        )
        return [{"key": r.tracker_key, "state": r.state, "fields": dict(r.fields)} for r in rows]


async def _decide(client: AsyncClient, item_id: str, kind: str, note: str, attest: str) -> None:
    request = await _request(client, item_id)
    assert (request["kind"], request["payload"]["attest"]) == ("task", attest)
    body: dict[str, Any] = (
        {"kind": "reject", "reason": note}
        if kind == "reject"
        else {"kind": kind, "values": {"decision_note": note}, "attested": True}
    )
    decided = await client.post(f"/api/v1/work-items/{item_id}/decisions", json=body)
    assert decided.status_code == 202, decided.text


CHILD_ATTESTATION = "I read the memo; this is an internal research decision, not advice"


async def test_a_trending_product_opens_a_child_the_owner_adopts_then_the_campaign_is_adopted(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    client = platform.client
    await login(client)
    project = await _organisation(client)
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": honest_analyst})
    platform.adapters.executor = bench
    fields = {"symbols": ["TRND.US", "AAA.US", "BBB.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3}

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        child = await _child(client, item["key"], 1)

        # The bars came from the provider through the platform, once per symbol, with the run's token.
        assert sorted(s for _, a in market.calls for s in [a["symbol"]]) == ["AAA.US", "BBB.US", "TRND.US"]
        jobs = [s for s in bench.started if s.stage_input and s.stage_input.job]
        roles = [s.stage_input.transition.role for s in jobs if s.stage_input]
        assert roles == ["snapshot", "profile", "backtest"]
        assert all(KEY not in s.model_dump_json() for s in bench.started), "no provider key reaches a run"

        # The verdict rests on the facts the backtest job MEASURED.
        from choregos_api.db.models import Run
        from choregos_api.db.session import session_scope
        from sqlalchemy import select

        async with session_scope() as session:
            backtest = (await session.execute(select(Run).where(Run.stage_role == "backtest"))).scalar_one()
            evidence = (backtest.result or {}).get("evidence") or {}
        assert {"facts.relevant_count", "facts.trials"} <= set(evidence.get("measured") or []), evidence
        assert evidence["facts"]["relevant_count"] == 1 and evidence["facts"]["trials"] == 9, evidence

        bars = await _artifact(client, item["id"], "bars")
        assert bars["type"] == "csv" and bars["digest"].startswith("sha256:"), bars

        # One child, for the one relevant product, in the template's second workflow, holding what the
        # backtest measured for THAT product — and only that one.
        assert (child["workflow"], child["fields"]["symbol"]) == ("product-memo", "TRND.US")
        assert child["fields"]["product"]["relevant"] is True
        assert "AAA.US" not in json.dumps(child["fields"]), "another product's numbers are not copied"
        await _state(client, child["id"], "memo_written", "the child reaches its memo")
        assert (await _artifact(client, child["id"], "memo_markdown"))["type"] == "markdown"
        (memo,) = [r.outputs.model_dump().get("memo_markdown") for r in bench.results.values()
                   if r.outputs.model_dump().get("memo_markdown")]  # fmt: skip
        assert "TRND.US" in memo and "AAA.US" not in memo, memo

        await _decide(client, child["id"], "complete", "follow TRND.US on paper", CHILD_ATTESTATION)
        await _state(client, item["id"], "products_settled", "the campaign resumes once its child is settled")
        assert [(c["key"], c["state"]) for c in await _children(item["key"])] == [(child["key"], "adopted")]

        await _decide(client, item["id"], "complete", "adopted on the owner's word", ATTESTATION)
        result = await interpreter.result()
        await _replayed(interpreter, "wi-campaign-adopted-S38-15.json")
    assert result["state"] == "adopted"
    timeline = (await client.get(f"/api/v1/work-items/{item['id']}/timeline")).text
    assert ATTESTATION in timeline, "the attestation is in the record"
    assert CHILD_ATTESTATION in (await client.get(f"/api/v1/work-items/{child['id']}/timeline")).text


async def test_two_relevant_products_open_two_children_and_the_campaign_waits_for_both(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    client = platform.client
    await login(client)
    project = await _organisation(client)
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": honest_analyst})
    platform.adapters.executor = bench
    fields = {"symbols": ["TRND.US", "TRN2.US", "AAA.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3}

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        first, second = await _child(client, item["key"], 1), await _child(client, item["key"], 2)
        assert {first["fields"]["symbol"], second["fields"]["symbol"]} == {"TRND.US", "TRN2.US"}
        assert {first["workflow"], second["workflow"]} == {"product-memo"}
        assert len(await _children(item["key"])) == 2, "exactly two children"

        # One child decided: the campaign does not pass.
        await _state(client, first["id"], "memo_written", "the first child reaches its memo")
        await _decide(client, first["id"], "complete", "follow it on paper", CHILD_ATTESTATION)
        await _state(client, first["id"], "adopted", "the first child is adopted")
        await asyncio.sleep(1.0)
        parent = (await client.get(f"/api/v1/work-items/{item['id']}")).json()
        assert parent["state"] == "relevant", (
            f"one child is still open: the campaign is in `{parent['state']}`"
        )

        # The second one rejected: both are settled, one succeeded — the campaign resumes.
        await _state(client, second["id"], "memo_written", "the second child reaches its memo")
        await _decide(client, second["id"], "reject", "too volatile", CHILD_ATTESTATION)
        await _state(client, second["id"], "rejected", "the second child is rejected")
        await _state(client, item["id"], "products_settled", "both children settled: the campaign resumes")
        states = {c["key"]: c["state"] for c in await _children(item["key"])}
        assert states == {first["key"]: "adopted", second["key"]: "rejected"}, states

        await _decide(client, item["id"], "complete", "one product adopted", ATTESTATION)
        result = await interpreter.result()
        await _replayed(interpreter, "wi-campaign-two-products-S38-15.json")
    assert result["state"] == "adopted"
    memos = [s for s in bench.started if s.stage_input and s.stage_input.transition.role == "memo"]
    assert len(memos) == 2, "one memo per relevant product, none for the campaign"


async def test_a_campaign_whose_every_product_is_rejected_ends_not_adopted(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    client = platform.client
    await login(client)
    project = await _organisation(client)
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": honest_analyst})
    platform.adapters.executor = bench
    fields = {"symbols": ["TRND.US", "AAA.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3}

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        child = await _child(client, item["key"], 1)
        await _state(client, child["id"], "memo_written", "the child reaches its memo")
        await _decide(client, child["id"], "reject", "no", CHILD_ATTESTATION)
        result = await interpreter.result()
    assert result["state"] == "not_adopted", result


async def test_random_walks_end_the_campaign_not_relevant_on_measured_facts(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    client = platform.client
    await login(client)
    project = await _organisation(client)
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": honest_analyst})
    platform.adapters.executor = bench
    fields = {"symbols": ["AAA.US", "BBB.US"], **PERIOD}

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        result = await interpreter.result()
        await _replayed(interpreter, "wi-campaign-not-relevant-S38-09.json")
    assert result["state"] == "not_relevant", result
    assert not [s for s in bench.started if s.stage_input and s.stage_input.transition.role == "memo"], (
        "no memo is written for a campaign that found nothing"
    )
    assert await _children(item["key"]) == [], "no product, no child"


async def test_a_memo_that_quotes_an_unmeasured_number_never_reaches_the_owner(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    client = platform.client
    await login(client)
    project = await _organisation(client)
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": inventive_analyst})
    platform.adapters.executor = bench
    fields = {"symbols": ["TRND.US", "AAA.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3}

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        child = await _child(client, item["key"], 1)
        await _state(client, child["id"], "to_review", "the invented number sends the product to review")
        await interpreter.cancel()
    memos = [s for s in bench.started if s.stage_input and s.stage_input.transition.role == "memo"]
    assert len(memos) == 2, "the analyst had its two attempts"


DIVERGING = {"source": "yahoo", "common_days": 700, "max_rel_diff": 0.31, "diverging_days": 4}


async def test_sources_that_disagree_send_the_campaign_to_review_before_any_backtest(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    """S38-14: the snapshot job measures the symbols whose two sources diverge; beyond the campaign's
    `max_diverging_symbols` the `t-snapshot` gate refuses and a person reviews. Without the gate the
    campaign would profile and backtest on closes the second source contradicts."""
    client = platform.client
    await login(client)
    project = await _organisation(client)
    market.cross_checks["TRND.US"] = DIVERGING
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": honest_analyst})
    platform.adapters.executor = bench
    fields = {"symbols": ["TRND.US", "AAA.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3}

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        await _state(client, item["id"], "to_review", "diverging sources send the campaign to review")
        await _replayed(interpreter, "wi-campaign-diverging-sources-S38-14.json")
        await interpreter.cancel()
    roles = [s.stage_input.transition.role for s in bench.started if s.stage_input]
    assert roles == ["snapshot"], f"nothing runs after a refused snapshot: {roles}"

    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        snapshot = (await session.execute(select(Run).where(Run.stage_role == "snapshot"))).scalar_one()
        evidence = (snapshot.result or {}).get("evidence") or {}
    assert {"facts.diverging", "facts.diverging_excess"} <= set(evidence.get("measured") or []), evidence
    assert evidence["facts"]["diverging_excess"] == 1 and evidence["facts"]["max_rel_diff"] == 0.31


async def test_a_divergence_within_the_campaign_threshold_goes_on(
    platform: Platform, worker: Any, market: Any, api_url: str, tmp_path: Path
) -> None:
    client = platform.client
    await login(client)
    project = await _organisation(client)
    market.cross_checks["TRND.US"] = DIVERGING
    bench = Bench(api_url, tmp_path / "runs", {"strategize": strategist, "memo": honest_analyst})
    platform.adapters.executor = bench
    fields = {
        "symbols": ["TRND.US", "AAA.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3,
        "max_diverging_symbols": 1,
    }  # fmt: skip

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        child = await _child(client, item["key"], 1)  # the campaign went past the snapshot gate
        assert child["fields"]["symbol"] == "TRND.US"
        await interpreter.cancel()
    roles = [s.stage_input.transition.role for s in bench.started if s.stage_input]
    assert roles[:3] == ["snapshot", "profile", "strategize"], roles


async def _until(what: str, condition: Callable[[], bool], tries: int = 1200) -> None:
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out: {what}")


def _engine_dies_after_computing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, times: int) -> None:
    """The first `times` runs of `choregos-quant backtest` compute for real, then exit non-zero (the
    pod died once its files were written): a crash of the engine, not a verdict. Done with a shim
    ahead of the real command on the PATH of the job's subprocess."""
    import shutil

    real = shutil.which("choregos-quant")
    assert real, "choregos-quant is not installed"
    bin_dir, left = tmp_path / "shim", tmp_path / "shim" / "deaths_left"
    bin_dir.mkdir()
    left.write_text(str(times), encoding="utf-8")
    shim = bin_dir / "choregos-quant"
    shim.write_text(
        f'#!/bin/sh\n"{real}" "$@"\ncode=$?\n[ "$1" = backtest ] && [ "$code" = 0 ] || exit "$code"\n'
        f'n=$(cat "{left}")\n[ "$n" -gt 0 ] || exit 0\necho $((n - 1)) > "{left}"\nexit 3\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


async def test_a_relaunched_campaign_reads_the_stored_snapshot_and_computes_the_same_metrics(
    platform: Platform,
    worker: Any,
    market: Any,
    api_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S38-16: the engine dies once after computing, so the campaign goes to review (`t-backtest`
    `on_fail`, `max_attempts: 1`); the owner relaunches it (`t-relaunch`) and the second round plays
    `t-snapshot` and `t-backtest` again on the same request. The provider is called by the first
    snapshot only; the second snapshot reuses the stored bars; both backtests read bars with the same
    SHA-256; the same job on the same snapshot gives the same metrics."""
    from choregos_api.db.models import ArtifactRecord, Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import artifact_digest
    from sqlalchemy import select

    client = platform.client
    await login(client)
    project = await _organisation(client)
    runs = tmp_path / "runs"
    bench = Bench(api_url, runs, {"strategize": strategist, "memo": honest_analyst})
    _engine_dies_after_computing(tmp_path, monkeypatch, times=1)
    platform.adapters.executor = bench
    fields = {"symbols": ["TRND.US", "AAA.US"], **PERIOD, "min_dsr": 0.9, "min_trades": 3}

    def played(role: str) -> list[StageJobSpec]:
        return [s for s in bench.started if s.stage_input and s.stage_input.transition.role == role]

    async with worker():
        item, interpreter = await _campaign(platform, project, fields)
        await _state(client, item["id"], "to_review", "the first round ends in review")
        assert len(played("backtest")) == 1, "the engine had its one attempt"
        first_calls = len(market.calls)
        assert sorted(a["symbol"] for _, a in market.calls) == ["AAA.US", "TRND.US"]
        assert (await _request(client, item["id"]))["kind"] == "approval"
        relaunched = await client.post(f"/api/v1/work-items/{item['id']}/decisions", json={"kind": "approve"})
        assert relaunched.status_code == 202, relaunched.text
        child = await _child(client, item["key"], 1)  # the second round went through to the fan-out
        assert child["fields"]["symbol"] == "TRND.US"
        await interpreter.cancel()

    assert [len(played(r)) for r in ("snapshot", "profile", "strategize", "backtest")] == [2, 2, 2, 2]
    # (1) The provider is called by the first snapshot only.
    assert len(market.calls) == first_calls == 2, market.calls
    async with session_scope() as session:
        snapshots = list(
            (
                await session.execute(
                    select(Run).where(Run.stage_role == "snapshot").order_by(Run.created_at)
                )
            ).scalars()
        )
        facts = [((r.result or {}).get("evidence") or {}).get("facts") or {} for r in snapshots]
        bars = list(
            (await session.execute(select(ArtifactRecord).where(ArtifactRecord.name == "bars"))).scalars()
        )
        metrics = list(
            (await session.execute(select(ArtifactRecord).where(ArtifactRecord.name == "metrics"))).scalars()
        )
    assert [f.get("reused") for f in facts] == [0, 1], facts
    # (2) Both backtests read, from their workspace, the very bytes the snapshot stored.
    assert len(bars) == 1 and bars[0].digest.startswith("sha256:"), "the stored bars did not change"
    read = [
        artifact_digest((runs / s.run_id / ".choregos" / "inputs" / "bars.csv").read_bytes())
        for s in played("backtest")
    ]
    assert read == [bars[0].digest] * 2, read
    # (3) The same job on the same snapshot, twice: byte-identical metrics, one revision.
    assert len(metrics) == 1, [(m.version, m.digest) for m in metrics]
    # The engine that died had computed for real: its files are the very bytes of the one that finished.
    died, finished = (runs / s.run_id / ".choregos" for s in played("backtest"))
    for name in ("job.json", "outputs/results.csv", "outputs/report.html"):
        assert (died / name).read_bytes() == (finished / name).read_bytes(), name
    await _replayed(interpreter, "wi-campaign-relaunched-S38-16.json")
