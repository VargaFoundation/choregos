"""A distillation is a job (S42-10e, ADR 0092): it can be stopped and relaunched, it keeps its last
runs, its spend is booked in the cost ledger, and a streamed merge no longer times out."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .test_knowledge_distillation_passes import BASE, FOUR, Counting, _base, _outcome, _price

KB = f"{BASE}/architecture"
CAPPED = {"input_tokens": 1000, "output_tokens": 500}


async def _ledger() -> list[Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import TOUT, session_scope
    from sqlalchemy import select

    async with session_scope(orgs=TOUT) as session:
        rows = (await session.execute(select(CostLedger).order_by(CostLedger.ts))).scalars().all()
        session.expunge_all()
        return list(rows)


async def _set_record(record: dict[str, Any] | None, **columns: Any) -> None:
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import TOUT, session_scope
    from sqlalchemy import select

    async with session_scope(orgs=TOUT) as session:
        base = (await session.execute(select(KnowledgeBase))).scalars().one()
        base.distillation = record
        for name, value in columns.items():
            setattr(base, name, value)


# ------------------------------------------------------------------------------------------- cost


async def test_every_call_made_is_booked_as_distill_on_the_organisation_without_an_owning_project(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.distill import distill
    from sqlalchemy import select

    base_id = await _base(client, FOUR, monkeypatch)
    await _price(0.01, 0.03)
    distiller = Counting(CAPPED)
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=distiller)
    rows = await _ledger()
    # one line per part call, and one for the whole merge phase (a tree of calls)
    assert outcome["status"] == "pending_review" and len(rows) == len(distiller.parts) + 1 > 2
    assert {r.kind for r in rows} == {"distill"}
    assert all(r.project_id is None and r.agent_slug == "knowledge-distiller" for r in rows)
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
    assert {r.org_id for r in rows} == {org_id}
    assert sum(r.cost_usd for r in rows) == pytest.approx(outcome["spent_usd"])
    assert outcome["spent_usd"] > 0
    assert sum(r.tokens_in for r in rows) == 1000 * (len(distiller.parts) + distiller.merges)


async def test_a_base_with_an_owning_project_books_on_that_project(
    client: AsyncClient, admin: str, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await _set_record(None, owner_project=project["slug"])
    await distill("varga", base_id, "admin@varga.dev", distiller=Counting(CAPPED))
    rows = await _ledger()
    assert rows and {(r.project_id, r.org_id) for r in rows} == {(project["id"], None)}
    mine = (await client.get(f"/api/v1/projects/{project['id']}/costs?kind=distill&group_by=kind")).json()
    assert [(r["key"], r["runs"]) for r in mine["rows"]] == [("distill", len(rows))]


async def test_an_owning_project_that_no_longer_exists_falls_back_to_the_organisation(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await _set_record(None, owner_project="deleted-project")
    await distill("varga", base_id, "admin@varga.dev", distiller=Counting(CAPPED))
    rows = await _ledger()
    assert rows and all(r.project_id is None and r.org_id for r in rows)


async def test_a_replay_and_a_relaunch_book_only_what_they_call(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await distill("varga", base_id, "admin@varga.dev", distiller=Counting(CAPPED))
    booked = len(await _ledger())
    await distill("varga", base_id, "admin@varga.dev", distiller=Counting(CAPPED))
    assert len(await _ledger()) == booked, "every call was kept: nothing is paid, nothing is booked"


async def test_the_organisation_report_shows_the_spend_and_the_project_report_does_not(
    client: AsyncClient, admin: str, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await _price(0.01, 0.03)
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=Counting(CAPPED))
    by_kind = (await client.get("/api/v1/orgs/varga/costs?group_by=kind")).json()
    assert [r["key"] for r in by_kind["rows"]] == ["distill"]
    assert by_kind["total"]["cost_usd"] == pytest.approx(outcome["spent_usd"], abs=1e-4)
    by_project = (await client.get("/api/v1/orgs/varga/costs?group_by=project&kind=distill")).json()
    assert [r["key"] for r in by_project["rows"]] == ["—"]
    assert (await client.get("/api/v1/orgs/varga/costs?kind=model")).json()["rows"] == []
    assert (await client.get(f"/api/v1/projects/{project['id']}/costs?kind=distill")).json()["rows"] == []
    assert (await client.get("/api/v1/orgs/varga/costs.csv?kind=distill")).status_code == 200


async def test_a_ledger_line_needs_a_project_or_an_organisation(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.couts import record_cost

    async with session_scope() as session:
        with pytest.raises(ValueError, match="project or, failing one, to an organisation"):
            await record_cost(
                session,
                project_id=None,
                work_item_id=None,
                run_id=None,
                provider="",
                model="m",
                backend=None,
                stage_role=None,
                size=None,
                tokens_in=0,
                tokens_out=0,
                tokens_cached=0,
                cost_usd=0.0,
                fx_rate=0.9,
            )


# ------------------------------------------------------------------------------------------ stop


async def test_stopping_a_running_distillation_cancels_the_workflow_and_says_who(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.temporal import get_temporal, knowledge_distill_id

    await _base(client, FOUR, monkeypatch)
    started = await client.post(KB + "/distill", json={})
    assert started.status_code == 202
    record = started.json()["distillation"]
    stopped = await client.post(KB + "/distill/stop")
    assert stopped.status_code == 202, stopped.text
    after = stopped.json()["distillation"]
    assert (after["status"], after["code"]) == ("stopped", "cancelled")
    assert after["stopped_by"] == record["requested_by"] and after["at"]
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import TOUT, session_scope
    from sqlalchemy import select

    async with session_scope(orgs=TOUT) as session:
        base_id = (await session.execute(select(KnowledgeBase.id))).scalar_one()
    assert knowledge_distill_id(base_id, record["started_at"]) in get_temporal().cancelled  # type: ignore[attr-defined]
    # Nothing runs any more: a second stop has nothing to stop.
    again = await client.post(KB + "/distill/stop")
    assert again.status_code == 409 and again.json()["code"] == "distill_not_running"


async def test_stopping_nothing_is_refused_and_so_is_a_stranger(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _base(client, FOUR, monkeypatch)
    assert (await client.post(KB + "/distill/stop")).status_code == 409
    assert (await client.post(f"{BASE}/unknown/distill/stop")).status_code == 404


async def test_a_stopped_run_makes_no_further_call_and_publishes_nothing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await _set_record(
        {"status": "running", "started_at": "2026-10-26T10:00:00+00:00", "requested_by": "admin@varga.dev"}
    )

    class StopsAfterTheFirstPart(Counting):
        async def principles(self, instructions: str, corpus: list[Any]) -> dict[str, Any]:
            answer = await super().principles(instructions, corpus)
            if len(self.parts) == 1:
                stopped = await client.post(KB + "/distill/stop")
                assert stopped.status_code == 202
            return answer

    distiller = StopsAfterTheFirstPart(CAPPED)
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=distiller)
    assert (outcome["status"], outcome["code"]) == ("stopped", "cancelled"), outcome
    assert len(distiller.parts) == 1 and distiller.merges == 0
    assert len(await _ledger()) == 1, "the call that was in flight is booked, none after it"
    assert (await _outcome(client))["stopped_by"] == "admin@varga.dev"
    assert (await client.get("/api/v1/orgs/varga/skills/architecture-principles")).status_code == 404


async def test_a_cancelled_answer_does_not_overwrite_a_new_running_record(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import step_fail

    base_id = await _base(client, FOUR, monkeypatch)
    now = {"status": "running", "started_at": "2026-10-26T11:00:00+00:00", "requested_by": "me"}
    await _set_record(now)
    answered = await step_fail("varga", base_id, "cancelled", "stopped", "me", 1.0)
    assert answered["status"] == "running" and (await _outcome(client))["status"] == "running"


# --------------------------------------------------------------------------- relaunch and history


async def test_a_relaunch_keeps_the_last_run_s_settings_and_its_outcome_joins_the_history(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.temporal import get_temporal

    await _base(client, FOUR, monkeypatch)
    failed = {
        "status": "failed",
        "code": "gateway",
        "error": "ReadTimeout",
        "requested_by": "someone@varga.dev",
        "started_at": "2026-10-26T10:00:00+00:00",
        "spent_usd": 4.2,
        "exclude": ["d3.md"],
        "max_usd": 7.5,
        "junk": "never kept in the history",
    }
    await _set_record(failed)
    relaunched = await client.post(KB + "/distill/relaunch")
    assert relaunched.status_code == 202, relaunched.text
    record = relaunched.json()["distillation"]
    assert record["status"] == "running" and record["requested_by"] == "admin@varga.dev"
    assert (record["exclude"], record["max_usd"]) == (["d3.md"], 7.5)
    assert [h["status"] for h in record["history"]] == ["failed"]
    assert record["history"][0]["spent_usd"] == 4.2 and "junk" not in record["history"][0]
    payloads = list(get_temporal().started.values())  # type: ignore[attr-defined]
    assert payloads[-1]["exclude"] == ["d3.md"] and payloads[-1]["max_usd"] == 7.5
    # While it runs, another relaunch is refused.
    assert (await client.post(KB + "/distill/relaunch")).status_code == 409


async def test_a_body_replaces_what_a_relaunch_would_reuse(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _base(client, FOUR, monkeypatch)
    await _set_record({"status": "stopped", "exclude": ["d3.md"], "max_usd": 7.5})
    sent = await client.post(KB + "/distill/relaunch", json={"exclude": ["d2.md"], "max_usd": 3})
    assert (sent.json()["distillation"]["exclude"], sent.json()["distillation"]["max_usd"]) == (["d2.md"], 3)


async def test_there_is_nothing_to_relaunch_before_a_first_run(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _base(client, FOUR, monkeypatch)
    refused = await client.post(KB + "/distill/relaunch")
    assert refused.status_code == 409 and refused.json()["code"] == "distill_nothing_to_relaunch"


async def test_a_relaunch_after_a_finished_run_reuses_every_stored_part_and_books_nothing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.conversion import conversions
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await distill("varga", base_id, "admin@varga.dev", distiller=Counting(CAPPED))
    booked = len(await _ledger())
    await client.post(KB + "/distill/relaunch")
    await conversions.drain()
    outcome = await _outcome(client)
    assert outcome["status"] == "pending_review" and outcome["spent_usd"] == 0
    assert [h["status"] for h in outcome["history"]] == ["pending_review"], "the previous outcome is kept"
    assert len(await _ledger()) == booked


async def test_the_history_keeps_five_runs_newest_first_and_the_outcome_keeps_it() -> None:
    from choregos_api.knowledge.distill import HISTORY_SIZE, new_run

    record: dict[str, Any] | None = None
    for n in range(8):
        record = new_run(record, "me", f"2026-10-26T10:0{n}:00+00:00", [], None)
        record["status"] = "failed"
        record["spent_usd"] = float(n)
    assert HISTORY_SIZE == 5 and record is not None
    assert [h["spent_usd"] for h in record["history"]] == [6.0, 5.0, 4.0, 3.0, 2.0]
    assert all("history" not in h for h in record["history"])


async def test_a_failed_run_and_a_published_one_keep_the_history_and_the_settings(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.conversion import conversions

    await _base(client, FOUR, monkeypatch)
    await _set_record({"status": "failed", "code": "gateway", "exclude": [], "max_usd": None})
    await client.post(KB + "/distill/relaunch")
    await conversions.drain()
    published = await _outcome(client)
    assert published["status"] == "pending_review" and [h["status"] for h in published["history"]] == [
        "failed"
    ]
    assert "exclude" in published and "max_usd" in published
    await client.post(KB + "/distill/relaunch", json={"max_usd": 0.0001})
    await conversions.drain()
    # An unpriced model estimates nothing: the cap does not refuse, the run publishes the same files.
    again = await _outcome(client)
    assert [h["status"] for h in again["history"]] == ["pending_review", "failed"]


# --------------------------------------------------------------------- a long merge is streamed


def _stream(chunks: list[dict[str, Any]]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def _gateway(answer: dict[str, Any]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not body.get("stream"):
            # What the gateway does with a long generation when nobody reads: silence, then the timeout.
            raise httpx.ReadTimeout("ReadTimeout", request=request)
        text = json.dumps(answer)
        chunks = [{"choices": [{"delta": {"content": text[i : i + 40]}}]} for i in range(0, len(text), 40)]
        chunks.append({"choices": [], "usage": {"prompt_tokens": 123, "completion_tokens": 45}})
        return httpx.Response(200, content=_stream(chunks), headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handle)


async def test_a_merge_is_streamed_so_a_long_generation_is_not_a_read_timeout() -> None:
    from choregos_api.knowledge.distill import GatewayDistiller

    answer = {"description": "d", "principles": [{"title": "T", "statement": "S", "sources": []}] * 30}
    distiller = GatewayDistiller("http://gw", "k", "m", transport=_gateway(answer))
    merged = await distiller.merge([{"title": "a"}])
    assert merged["principles"] == answer["principles"]
    assert merged["usage"] == {"input_tokens": 123, "output_tokens": 45}


async def test_a_gateway_that_ignores_stream_still_answers() -> None:
    from choregos_api.knowledge.distill import DistillationError, GatewayDistiller

    def plain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"principles": []}'}}], "usage": {}}
        )

    answer = await GatewayDistiller("http://gw", "k", "m", transport=httpx.MockTransport(plain)).merge([])
    assert answer["principles"] == []
    refusing = httpx.MockTransport(lambda request: httpx.Response(503, text="down"))
    with pytest.raises(DistillationError, match="503"):
        await GatewayDistiller("http://gw", "k", "m", transport=refusing).merge([])
