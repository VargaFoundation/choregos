"""Distillation in passes (S42-10c, ADR 0082): a base too big for one prompt is cut into parts, each
part distilled once (kept by digest), the results merged, a budget checked before every call, and
what would be read and paid known before the click."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

BASE = "/api/v1/orgs/varga/knowledge"
SECTION = "Every {topic} change is reviewed by its owner and recorded in the register before release."


def doc(topic: str, sections: int) -> bytes:
    parts = [f"# {topic.title()} standard\n"]
    parts += [f"## {topic} rule {n}\n\n{SECTION.format(topic=f'{topic} {n}')}\n" for n in range(sections)]
    return "\n".join(parts).encode()


def source(name: str, markdown: str) -> Any:
    from choregos_api.knowledge.distill import Source

    return Source(name, name, f"sha256:{name}", markdown)


# ----------------------------------------------------------------------------------------- planning


def test_every_part_stays_under_the_limit_and_every_section_lands_once() -> None:
    from choregos_api.knowledge.distill import plan

    corpus = [source(f"d{n}.md", doc(f"topic{n}", 4).decode()) for n in range(6)]
    parts = plan(corpus, 1500)
    assert len(parts) > 1
    assert all(part.characters <= 1500 for part in parts)
    assert [name for p in parts for name in p.documents] == [f"d{n}.md" for n in range(6)]
    text = "\n".join(p.text for p in parts)
    for n in range(6):
        for rule in range(4):
            assert text.count(f"topic{n} rule {rule}\n") == 1


def test_a_big_document_is_cut_on_its_headings() -> None:
    from choregos_api.knowledge.distill import plan

    big = source("big.md", doc("network", 30).decode())
    parts = plan([big], 1500)
    assert len(parts) > 2
    assert all(p.characters <= 1500 for p in parts)
    for part in parts:
        for piece in part.text.split("=== DOCUMENT: ")[1:]:
            body = piece.split("===\n", 1)[1]
            assert body.lstrip().startswith("#"), "a piece starts at a heading, not inside a section"
            assert "big.md | big.md (part " in piece
    assert "".join(p.text for p in parts).count("## network rule") == 30


def test_excluded_documents_are_left_out_by_glob_not_by_case() -> None:
    from choregos_api.knowledge.distill import plan

    corpus = [
        source("Budget.XLSX", "# a\n\ntext"),
        source("plan.md", "# b\n\ntext"),
        source("old/x.md", "# c"),
    ]
    parts = plan(corpus, 10_000, ["*.xlsx", "old/*"])
    assert [name for p in parts for name in p.documents] == ["plan.md"]


# ------------------------------------------------------------------------------------------ reducing


async def test_the_merge_dedupes_unions_the_sources_and_never_cites_outside_the_corpus() -> None:
    from choregos_api.knowledge.distill import FakeDistiller, _merge_tree
    from choregos_api.services.model_catalogue import Price

    class Rogue(FakeDistiller):
        async def merge(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
            answer = await super().merge(candidates)
            answer["principles"][0]["sources"].append({"document": "made-up.pdf", "section": "1"})
            return answer

    corpus = [source("a.md", "# a"), source("b.md", "# b")]
    candidates = [
        {
            "title": "T",
            "statement": "Review every change.",
            "sources": [{"document": "a.md", "section": "x"}],
        },
        {
            "title": "T2",
            "statement": "review every change",
            "sources": [{"document": "b.md", "section": "y"}],
        },
        {"title": "U", "statement": "Record it.", "sources": [{"document": "b.md", "section": "z"}]},
    ]
    merged, _, _, _ = await _merge_tree(Rogue(), candidates, corpus, Price(None, None, "none"), 100_000)
    assert [p["statement"] for p in merged] == ["Review every change.", "Record it."]
    assert merged[0]["sources"] == [
        {"document": "a.md", "section": "x"},
        {"document": "b.md", "section": "y"},
    ]


# ----------------------------------------------------------------------------------- the whole run


class Counting:
    """A distiller that counts its calls and can be told to under-report its price."""

    def __init__(self, usage: dict[str, int] | None = None) -> None:
        from choregos_api.knowledge.distill import FakeDistiller

        self.inner, self.usage = FakeDistiller(), usage
        self.model = self.inner.model
        self.parts: list[list[str]] = []
        self.merges = 0

    async def principles(self, instructions: str, corpus: list[Any]) -> dict[str, Any]:
        self.parts.append([s.filename for s in corpus])
        answer = await self.inner.principles(instructions, corpus)
        return {**answer, "usage": self.usage or answer["usage"]}

    async def merge(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        self.merges += 1
        answer = await self.inner.merge(candidates)
        return {**answer, "usage": self.usage or answer["usage"]}


async def _base(client: AsyncClient, files: dict[str, bytes], monkeypatch: pytest.MonkeyPatch) -> str:
    from choregos_api.config import get_settings
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.conversion import conversions
    from sqlalchemy import select

    monkeypatch.setattr(get_settings(), "knowledge_distill_part_chars", 2500)
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    for name, data in files.items():
        assert (
            await client.post(f"{BASE}/architecture/documents", files={"file": (name, data)})
        ).status_code == 202
    await conversions.drain()
    async with session_scope() as session:
        return (await session.execute(select(KnowledgeBase.id))).scalar_one()


async def _price(per_1k_in: float, per_1k_out: float) -> None:
    from choregos_api.db.models import ModelCatalogueRow, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        session.add(
            ModelCatalogueRow(
                org_id=org_id,
                model_name="fake/distiller",
                usages=["agent", "distill"],
                input_per_1k_override=per_1k_in,
                output_per_1k_override=per_1k_out,
            )
        )


FOUR = {f"d{n}.md": doc(f"topic{n}", 5) for n in range(4)}


async def _outcome(client: AsyncClient) -> dict[str, Any]:
    return dict((await client.get(f"{BASE}/architecture")).json()["distillation"])


async def test_a_base_bigger_than_a_prompt_is_distilled_in_passes(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    distiller = Counting()
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=distiller)
    assert outcome["status"] == "pending_review", outcome
    assert outcome["parts_total"] == len(distiller.parts) > 1 and outcome["phase"] == "publish"
    assert distiller.merges >= 1
    assert sorted({name for part in distiller.parts for name in part}) == sorted(FOUR)
    files = (await client.get("/api/v1/orgs/varga/skills/architecture-principles/versions/1")).json()["files"]
    for name in FOUR:
        assert f"`{name}`" in files["references/sources.md"]


async def test_the_route_runs_the_same_steps_and_reports_the_progress(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.conversion import conversions

    await _base(client, FOUR, monkeypatch)
    started = await client.post(f"{BASE}/architecture/distill", json={"exclude": ["d3.md"], "max_usd": 5})
    assert started.status_code == 202, started.text
    assert started.json()["distillation"]["status"] == "running"
    await conversions.drain()
    outcome = await _outcome(client)
    assert outcome["status"] == "pending_review" and outcome["parts_done"] == outcome["parts_total"] >= 1
    assert "d3.md" not in {d["filename"] for d in outcome["documents"]}


async def test_a_replayed_distillation_makes_no_second_call_and_no_second_version(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    first = Counting()
    await distill("varga", base_id, "admin@varga.dev", distiller=first)
    again = Counting()
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=again)
    assert first.parts and not again.parts and again.merges == 0, "every call was kept by its digest"
    assert outcome["status"] == "pending_review" and outcome["version"] == 1
    assert outcome["spent_usd"] == 0


async def test_a_changed_document_pays_for_its_own_part_only(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    first = Counting()
    await distill("varga", base_id, "admin@varga.dev", distiller=first)
    listed = (await client.get(f"{BASE}/architecture")).json()["documents"]
    old = next(d for d in listed if d["filename"] == "d2.md")
    assert (await client.delete(f"{BASE}/architecture/documents/{old['id']}")).status_code == 204
    changed = doc("topic2", 5) + b"\n## One more\n\nEvery exception expires after ninety days.\n"
    await client.post(f"{BASE}/architecture/documents", files={"file": ("d2.md", changed)})
    from choregos_api.knowledge.conversion import conversions

    await conversions.drain()
    second = Counting()
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=second)
    assert outcome["status"] == "pending_review" and outcome["version"] == 2
    assert second.parts and all("d2.md" in part for part in second.parts)
    assert len(second.parts) < len(first.parts)


async def test_the_budget_stops_the_run_before_the_next_call_and_publishes_nothing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.knowledge.distill import distill

    base_id = await _base(client, FOUR, monkeypatch)
    await _price(0.01, 0.03)
    monkeypatch.setattr(get_settings(), "knowledge_distill_budget_usd", 5.0)
    greedy = Counting({"input_tokens": 100_000, "output_tokens": 100_000})  # $4 a call, estimated far less
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=greedy)
    assert outcome["status"] == "failed" and outcome["code"] == "over_budget", outcome
    assert len(greedy.parts) == 2 and greedy.merges == 0
    assert outcome["spent_usd"] == pytest.approx(8.0)
    assert (await client.get("/api/v1/orgs/varga/skills/architecture-principles")).status_code == 404


async def test_a_part_that_the_gateway_refuses_fails_the_run_and_publishes_nothing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import DistillationError, distill

    class Down(Counting):
        async def principles(self, instructions: str, corpus: list[Any]) -> dict[str, Any]:
            raise DistillationError("the gateway answered 503: down")

    base_id = await _base(client, FOUR, monkeypatch)
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=Down())
    assert outcome["status"] == "failed" and outcome["code"] == "gateway" and "503" in outcome["error"]
    assert (await client.get("/api/v1/orgs/varga/skills/architecture-principles")).status_code == 404


async def test_a_part_keeps_only_the_principles_citing_its_own_documents(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.knowledge.distill import distill

    class Wandering(Counting):
        async def principles(self, instructions: str, corpus: list[Any]) -> dict[str, Any]:
            return {
                "description": "d",
                "principles": [
                    {"title": "Own", "statement": "Own rule.",
                     "sources": [{"document": corpus[0].filename, "section": "s"}]},
                    {"title": "Other", "statement": "Rule of another part.",
                     "sources": [{"document": "d9.md", "section": "s"}]},
                ],
            }  # fmt: skip

    base_id = await _base(client, FOUR, monkeypatch)
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=Wandering())
    assert outcome["status"] == "pending_review"
    assert any("Other" in line for line in outcome["dropped"])
    skill = (await client.get("/api/v1/orgs/varga/skills/architecture-principles/versions/1")).json()
    assert "Rule of another part." not in skill["files"]["SKILL.md"]


# --------------------------------------------------------------------------------------- the estimate


async def test_the_estimate_counts_the_parts_the_calls_and_prices_them(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _base(client, FOUR, monkeypatch)
    unpriced = (await client.get(f"{BASE}/architecture/distill/estimate")).json()
    assert unpriced["documents"] == 4 and unpriced["parts"] > 1
    assert unpriced["calls"] == unpriced["parts"] + 1 and unpriced["model"] == "fake/distiller"
    assert unpriced["priced_by"] == "none" and unpriced["input_cost_usd"] is None
    assert unpriced["within_budget"] is True and unpriced["refused_reason"] is None
    assert len(unpriced["biggest"]) == 4

    await _price(0.01, 0.03)
    priced = (await client.get(f"{BASE}/architecture/distill/estimate")).json()
    assert priced["priced_by"] == "override"
    assert priced["output_cost_usd"] == pytest.approx(priced["calls"] * 6000 / 1000 * 0.03)
    assert priced["input_cost_usd"] > 0

    fewer = (
        await client.get(f"{BASE}/architecture/distill/estimate", params={"exclude": ["d2.md", "d3.md"]})
    ).json()
    assert fewer["documents"] == 2 and fewer["parts"] < priced["parts"]
    assert len(fewer["biggest"]) == 4, "the console can still toggle an excluded document back"


async def test_a_cap_below_the_estimate_is_refused_before_anything_starts(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _base(client, FOUR, monkeypatch)
    await _price(0.01, 0.03)
    refused = await client.post(f"{BASE}/architecture/distill", json={"max_usd": 0.01})
    assert refused.status_code == 409 and refused.json()["code"] == "distill_over_budget"
    assert (await client.get(f"{BASE}/architecture")).json()["distillation"] is None


async def test_a_base_needing_more_parts_than_the_cap_is_refused_with_its_biggest_documents(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.knowledge.conversion import conversions

    await _base(client, FOUR, monkeypatch)
    monkeypatch.setattr(get_settings(), "knowledge_distill_max_parts", 1)
    estimate = (await client.get(f"{BASE}/architecture/distill/estimate")).json()
    assert "too large" not in estimate["refused_reason"]  # it says what to do instead
    assert "exclude" in estimate["refused_reason"] and "d0.md" in estimate["refused_reason"]
    started = await client.post(f"{BASE}/architecture/distill")
    assert started.status_code == 202
    await conversions.drain()
    outcome = await _outcome(client)
    assert outcome["status"] == "failed" and outcome["code"] == "too_large"
    # Leaving documents out makes it fit.
    await client.post(f"{BASE}/architecture/distill", json={"exclude": ["d2.md", "d3.md"]})
    await conversions.drain()
    assert (await _outcome(client))["status"] == "pending_review"


async def test_a_second_start_while_one_runs_is_refused(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import TOUT, session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    await _base(client, FOUR, monkeypatch)
    async with session_scope(orgs=TOUT) as session:
        base = (await session.execute(select(KnowledgeBase))).scalars().one()
        base.distillation = {"status": "running", "started_at": utcnow().isoformat()}
    assert (await client.post(f"{BASE}/architecture/distill", json={"exclude": ["d3.md"]})).status_code == 409


async def test_the_workflow_id_of_each_request_is_new() -> None:
    from choregos_api.temporal import knowledge_distill_id

    first = knowledge_distill_id("b1", "2026-10-21T10:00:00+00:00")
    assert first.startswith("knowledge-distill-b1-")
    assert first != knowledge_distill_id("b1", "2026-10-21T10:00:01+00:00")
