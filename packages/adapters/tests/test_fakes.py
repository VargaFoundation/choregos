"""Les fakes respectent les Protocol et se comportent comme les vrais adaptateurs."""

from __future__ import annotations

import json

import pytest
from choregos_adapters import (
    AdapterSet,
    CdAdapter,
    CiAdapter,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)
from choregos_adapters.fakes import BudgetExceeded
from choregos_contracts import Size, StageResult, StageStatus
from choregos_core.domain import Change, Fact, Message, NewItem, Provenance, StageJobSpec, TrackerStateMapping


def test_adapter_set_satisfies_protocols() -> None:
    adapters = AdapterSet.fakes()
    assert isinstance(adapters.tracker, TrackerAdapter)
    assert isinstance(adapters.scm, ScmAdapter)
    assert isinstance(adapters.ci, CiAdapter)
    assert isinstance(adapters.cd, CdAdapter)
    assert isinstance(adapters.executor, Executor)
    assert isinstance(adapters.memory, MemoryAdapter)
    assert isinstance(adapters.gateway, GatewayAdapter)
    assert isinstance(adapters.notify, Notifier)


async def test_tracker_lifecycle() -> None:
    tracker = AdapterSet.fakes().tracker
    key = tracker.seed("Corriger les avoirs", "Le total ignore l'avoir", size=Size.M)  # type: ignore[attr-defined]
    item = await tracker.fetch_item(key)
    assert item.title == "Corriger les avoirs"

    await tracker.set_state(key, TrackerStateMapping(status="In progress", label="choregos:in_progress"))
    item = await tracker.fetch_item(key)
    assert item.state == "In progress"
    assert "choregos:in_progress" in item.labels

    marker = "<!-- choregos:status -->"
    await tracker.upsert_status_comment(key, "v1", marker)
    await tracker.upsert_status_comment(key, "v2", marker)
    item = await tracker.fetch_item(key)
    assert len([c for c in item.comments if c.marker == marker]) == 1, "un seul commentaire de statut"
    assert tracker.status_comment(key) == "v2"  # type: ignore[attr-defined]

    await tracker.set_fields(key, {"Coût (€)": 6.77, "Taille": "M"})
    assert (await tracker.fetch_item(key)).fields["Coût (€)"] == 6.77

    child = await tracker.create_item(NewItem(title="Finding : N+1", body="…", labels=["finding"]))
    await tracker.link(child, key, "origin")
    assert (child, key, "origin") in tracker.links  # type: ignore[attr-defined]


async def test_tracker_webhook_and_move() -> None:
    tracker = AdapterSet.fakes().tracker
    key = tracker.seed("T")  # type: ignore[attr-defined]
    event = tracker.move(key, "Ready")  # type: ignore[attr-defined]
    assert event.type == "tracker.item.moved"
    assert event.payload["to_status"] == "Ready"
    body = json.dumps(event.model_dump(mode="json")).encode()
    assert tracker.verify_webhook({"X-Fake-Secret": "fake-secret"}, body)
    assert not tracker.verify_webhook({}, body)
    assert tracker.parse_webhook({}, body)[0].work_item_key == key


async def test_scm_pr_flow() -> None:
    scm = AdapterSet.fakes().scm
    await scm.ensure_branch("acme/billing", "choregos/123", "main")
    ref = await scm.open_pr("acme/billing", "choregos/123", "main", "fix: avoirs", "corps", True)
    await scm.update_pr(ref, body="corps v2", draft=False)
    scm.set_checks(ref, "success")  # type: ignore[attr-defined]
    scm.submit_review(ref, "marie", "approved")  # type: ignore[attr-defined]
    pr = await scm.get_pr(ref)
    assert pr.checks_conclusion() == "success"
    assert pr.review_conclusion() == "approved"
    assert not pr.draft
    await scm.enqueue_merge(ref)
    assert scm.merge_queue == [ref]  # type: ignore[attr-defined]
    token = await scm.mint_token("acme/billing", 3600, ["contents:write"])
    assert token.startswith("fake-token-")


async def test_scm_diff_and_scope() -> None:
    scm = AdapterSet.fakes().scm
    scm.set_diff("acme/billing", "main", "choregos/123", [("src/a.py", 10, 2), ("docs/x.md", 1, 0)])  # type: ignore[attr-defined]
    diff = await scm.compare("acme/billing", "main", "choregos/123")
    assert diff.paths() == ["src/a.py", "docs/x.md"]
    assert diff.additions == 11 and diff.deletions == 2


async def test_gateway_budget_is_a_hard_cap() -> None:
    gateway = AdapterSet.fakes().gateway
    key = await gateway.mint_key({"run_id": "r1"}, budget_usd=0.05, ttl_s=600, models=["platform/standard"])
    gateway.record_usage(key.key_id, tokens_in=1000, tokens_out=100)  # type: ignore[attr-defined]
    spend = await gateway.spend(key.key_id)
    assert spend.cost_usd == pytest.approx(0.003 + 0.0015)
    assert spend.requests == 1
    with pytest.raises(BudgetExceeded):
        gateway.record_usage(key.key_id, tokens_in=100_000, tokens_out=10_000)  # type: ignore[attr-defined]
    await gateway.revoke(key.key_id)
    assert key.key_id in gateway.revoked  # type: ignore[attr-defined]
    assert len(await gateway.list_models()) >= 3


async def test_executor_is_idempotent_by_run_id() -> None:
    executor = AdapterSet.fakes().executor
    executor.queue_result(StageResult(status=StageStatus.DONE, summary="ok"))  # type: ignore[attr-defined]
    spec = StageJobSpec(
        run_id="run-1",
        project_slug="demo",
        namespace="proj-demo-runners",
        runner_image="img",
        api_url="http://api/internal",
        run_token="tok",
    )
    first = await executor.start(spec)
    second = await executor.start(spec)
    assert first.run_id == second.run_id
    assert len(executor.started) == 1, "un seul démarrage réel pour un même run_id"  # type: ignore[attr-defined]
    assert (await executor.status(first)).state == "succeeded"
    await executor.cancel(first)
    assert (await executor.status(first)).state == "cancelled"
    lines = [line async for line in executor.logs(first)]
    assert lines


async def test_memory_context_pack_respects_budget() -> None:
    memory = AdapterSet.fakes().memory
    memory.seed(
        "demo", "decision", "decision:billing:arrondis", "Les totaux sont arrondis à l'émission." * 20
    )  # type: ignore[attr-defined]
    memory.seed("demo", "incident", "incident:billing:2026-06", "Rollback sur les avoirs." * 20)  # type: ignore[attr-defined]
    pack = await memory.context_pack("demo", "avoirs arrondis totaux", [], budget_tokens=200)
    assert pack.tokens_estimated <= 200
    assert pack.truncated or pack.memories or pack.incidents


async def test_memory_supersession_and_pending() -> None:
    memory = AdapterSet.fakes().memory
    await memory.write_fact("demo", Fact(kind="decision", subject="decision:x", content="v1"))
    await memory.write_fact("demo", Fact(kind="decision", subject="decision:x", content="v2"))
    actives = [m for m in memory.facts["demo"] if m.status == "active"]  # type: ignore[attr-defined]
    assert len(actives) == 1 and actives[0].content == "v2"

    pending_id = await memory.propose_fact(
        "demo",
        Fact(kind="convention", subject="convention:y", content="proposé"),
        Provenance(source="agent", run_id="run-1"),
    )
    assert memory.pending["demo"][0].id == pending_id  # type: ignore[attr-defined]
    assert await memory.accept_pending("demo", pending_id)  # type: ignore[attr-defined]
    assert any(m.subject == "convention:y" for m in await memory.search("demo", "convention", 5))


async def test_memory_ingest_is_idempotent() -> None:
    memory = AdapterSet.fakes().memory
    event = {"external_id": "gh:123", "kind": "ticket_summary", "subject": "ticket:123", "content": "résumé"}
    await memory.ingest_events("demo", [event, event])
    assert len(memory.facts["demo"]) == 1  # type: ignore[attr-defined]
    await memory.ingest_events("demo", [{**event, "content": "résumé v2"}])
    assert len([m for m in memory.facts["demo"] if m.status == "active"]) == 1  # type: ignore[attr-defined]


async def test_memory_failure_returns_empty_pack() -> None:
    memory = AdapterSet.fakes().memory
    memory.seed("demo", "decision", "d", "contenu")  # type: ignore[attr-defined]
    memory.fail = True  # type: ignore[attr-defined]
    pack = await memory.context_pack("demo", "quoi que ce soit", [], 1000)
    assert pack.is_empty(), "une panne mémoire ne doit jamais bloquer un stage"


async def test_cd_promotion_and_rollback() -> None:
    cd = AdapterSet.fakes().cd
    ref = await cd.promote("prod", [Change(app="billing-api", tag="v1.2.3")], "R-2026.09.18-1")
    assert ref.url and ref.merged
    assert (await cd.current_revision("billing-api")) == "v1.2.3"
    assert (await cd.health("billing-api")).status == "Healthy"
    cd.fail_next_analysis()  # type: ignore[attr-defined]
    assert (await cd.rollout_status("billing-api")).phase == "Degraded"
    await cd.abort_rollout("billing-api")
    assert "billing-api" in cd.aborted  # type: ignore[attr-defined]


async def test_ci_status_and_events() -> None:
    ci = AdapterSet.fakes().ci
    ci.set_status("acme/billing", "sha1", "failure", logs="ligne1\nligne2\nERREUR")  # type: ignore[attr-defined]
    status = await ci.status_for("acme/billing", "sha1")
    assert status.state == "failure"
    assert "ERREUR" in await ci.logs("acme/billing@sha1", tail=1)
    events = ci.parse_event({"ce-id": "x"}, json.dumps({"state": "failed"}).encode())
    assert events[0].type == "ci.run.failed"


async def test_notifier() -> None:
    notifier = AdapterSet.fakes().notify
    await notifier.send("#choregos", Message(title="Approbation requise", severity="warning"))
    assert notifier.last().title == "Approbation requise"  # type: ignore[union-attr]
