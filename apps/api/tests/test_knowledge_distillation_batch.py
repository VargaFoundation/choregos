"""The map step of a distillation in a batch (S4-09, ADR 0190): one batch for the parts, submitted once,
answers mapped by `custom_id`, booked at half the price, the synchronous path kept as the fallback."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_adapters.fakes.batch import FakeBatches

from .test_knowledge_distillation_ops import _ledger, _set_record
from .test_knowledge_distillation_passes import FOUR, _base, _price

KEY = ("northwind", "admin@northwind.dev")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeBatches:
    from tranibor_api.config import get_settings
    from tranibor_api.knowledge import distill

    batches = FakeBatches()
    monkeypatch.setattr(distill, "FAKE_BATCHES", batches)
    monkeypatch.setattr(get_settings(), "knowledge_distill_batch", True)
    monkeypatch.setattr(get_settings(), "knowledge_distill_batch_poll_seconds", 0)
    return batches


async def running(base_id: str) -> None:
    """What the caller of `distill` does first: the record says `running` (batch progress lands only then)."""
    from tranibor_api.knowledge.distill import new_run

    await _set_record(new_run(None, KEY[1], "2026-11-18T10:00:00+00:00", [], None))


@pytest.fixture
def sync_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The synchronous map calls made, by the documents they read."""
    from tranibor_api.knowledge.distill import FakeDistiller

    calls: list[list[str]] = []
    original = FakeDistiller.principles

    async def spy(self: Any, instructions: str, corpus: list[Any]) -> dict[str, Any]:
        calls.append([s.filename for s in corpus])
        return await original(self, instructions, corpus)

    monkeypatch.setattr(FakeDistiller, "principles", spy)
    return calls


async def test_the_parts_go_in_one_batch_and_no_part_is_called_synchronously(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches, sync_calls: Any
) -> None:
    from tranibor_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await running(base_id)
    outcome = await distill(*KEY[:1], base_id, KEY[1])
    assert outcome["status"] == "pending_review", outcome
    assert len(fake.submitted) == 1, "one batch for every part"
    _, requests = fake.submitted[0]
    assert len(requests) == outcome["parts_total"] > 1
    assert sync_calls == [], "no part was asked synchronously"


async def test_results_are_mapped_by_custom_id_not_by_position(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches
) -> None:
    """The fake answers in reverse; each stored part must hold the principle of ITS documents."""
    from sqlalchemy import select
    from tranibor_api.db.models import KnowledgeDistillationPart
    from tranibor_api.db.session import ALL_ORGS, session_scope
    from tranibor_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await running(base_id)
    await distill(*KEY[:1], base_id, KEY[1])
    async with session_scope(orgs=ALL_ORGS) as session:
        rows = (
            (
                await session.execute(
                    select(KnowledgeDistillationPart).where(KnowledgeDistillationPart.part_index >= 0)
                )
            )
            .scalars()
            .all()
        )
        stored = {r.part_index: r for r in rows}
    assert len(stored) == len(fake.submitted[0][1])
    for index, row in stored.items():
        cited = {s["document"] for p in row.answer["principles"] for s in p["sources"]}
        assert cited and cited <= set(row.documents), f"part {index} holds another part's answer"
        assert row.answer["batch"] == "fakebatch_1"


async def test_a_batched_part_is_booked_at_half_the_price_with_its_batch_usage(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches
) -> None:
    from tranibor_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await _price(0.01, 0.03)
    await running(base_id)
    outcome = await distill(*KEY[:1], base_id, KEY[1])
    assert outcome["status"] == "pending_review"
    parts = [r for r in await _ledger() if r.kind == "distill"][
        : len(fake.submitted[0][1])
    ]  # the merge is sync
    full = sum(r.tokens_in / 1000 * 0.01 + r.tokens_out / 1000 * 0.03 for r in parts)
    assert parts and full > 0
    assert sum(r.cost_usd for r in parts) == pytest.approx(full / 2), "50 % off, every token"


async def test_a_restart_polls_the_batch_it_has_and_submits_no_second(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches
) -> None:
    from tranibor_api.knowledge.distill import step_collect_batch, step_plan, step_submit_batch

    base_id = await _base(client, FOUR, monkeypatch)

    await running(base_id)
    assert (await step_plan(KEY[0], base_id, []))["batch"] is True
    first = await step_submit_batch(KEY[0], base_id, [])
    assert first["status"] == "submitted" and first["resumed"] is False
    # the worker restarted: the same activity runs again
    again = await step_submit_batch(KEY[0], base_id, [])
    assert again["batch_id"] == first["batch_id"] and again["resumed"] is True
    assert len(fake.submitted) == 1
    assert (await step_collect_batch(KEY[0], base_id, []))["status"] == "in_progress"
    done = await step_collect_batch(KEY[0], base_id, [])
    assert done["status"] == "done" and len(done["digests"]) == len(fake.submitted[0][1])
    # a collect that runs twice books nothing twice
    booked = len(await _ledger())
    await step_collect_batch(KEY[0], base_id, [])
    assert len(await _ledger()) == booked


async def test_a_part_the_batch_could_not_answer_falls_back_to_one_synchronous_call(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches, sync_calls: Any
) -> None:
    from tranibor_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    fake.outcomes["part-0"] = "expired"
    await running(base_id)
    outcome = await distill(*KEY[:1], base_id, KEY[1])
    assert outcome["status"] == "pending_review", outcome
    assert len(sync_calls) == 1, "only the expired part is asked synchronously"


async def test_a_batch_the_provider_refuses_leaves_the_synchronous_path(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches, sync_calls: Any
) -> None:
    from tranibor_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    fake.fail_submit = True
    await running(base_id)
    outcome = await distill(*KEY[:1], base_id, KEY[1])
    assert outcome["status"] == "pending_review" and len(sync_calls) == outcome["parts_total"]


async def test_off_by_default_nothing_is_batched(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, sync_calls: Any
) -> None:
    from tranibor_api.config import get_settings
    from tranibor_api.knowledge import distill as module

    assert get_settings().knowledge_distill_batch is False
    batches = FakeBatches()
    monkeypatch.setattr(module, "FAKE_BATCHES", batches)
    base_id = await _base(client, FOUR, monkeypatch)
    await running(base_id)
    outcome = await module.distill(*KEY[:1], base_id, KEY[1])
    assert outcome["status"] == "pending_review" and batches.submitted == [] and sync_calls


async def test_a_stopped_run_cancels_the_batch_still_out(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, fake: FakeBatches
) -> None:
    from tranibor_api.knowledge.distill import (
        _record,
        step_fail,
        step_plan,
        step_submit_batch,
        stopped_record,
    )

    base_id = await _base(client, FOUR, monkeypatch)
    await running(base_id)
    await step_plan(KEY[0], base_id, [])
    sent = await step_submit_batch(KEY[0], base_id, [])
    await _set_record(stopped_record(await _record(KEY[0], base_id), KEY[1]))
    await step_fail(KEY[0], base_id, "cancelled", "stopped", KEY[1])
    assert fake.cancelled == [sent["batch_id"]]
