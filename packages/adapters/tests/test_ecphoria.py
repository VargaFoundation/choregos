"""Ecphoria: reads with a short timeout that never block, governed writes, circuit breaker."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters.memory.ecphoria import EcphoriaMemory, _memory
from choregos_core.domain import Fact, Provenance

from ._transport import Fil

URL = "http://ecphoria.test"


def memory_for(fil: Fil, **kw: object) -> EcphoriaMemory:
    return EcphoriaMemory(URL, token="tok", client=fil.client(), **kw)  # type: ignore[arg-type]


async def test_the_context_pack_carries_the_tenant_and_translates_the_answer() -> None:
    fil = Fil(
        {
            ("POST", "/api/v1/context-pack"): (
                200,
                {
                    "tokens_estimated": 120,
                    "truncated": True,
                    "memories": [
                        {"id": "m1", "kind": "convention", "subject": "s", "content": "c", "score": 0.9}
                    ],
                    "related_items": [{"key": "K-1", "title": "t"}],
                },
            )
        }
    )
    pack = await memory_for(fil).context_pack("billing", "credit notes", ["src/"], 2000, kinds=["convention"])
    assert pack.tokens_estimated == 120 and pack.truncated and pack.memories[0].id == "m1"
    assert pack.related_items[0].key == "K-1"
    assert fil.requetes[0].headers["X-Ecphoria-Tenant"] == "billing"
    assert fil.requetes[0].headers["Authorization"] == "Bearer tok"
    assert fil.corps(0)["budget_tokens"] == 2000 and fil.corps(0)["kinds"] == ["convention"]


async def test_an_outage_returns_an_empty_pack_and_three_open_the_breaker() -> None:
    fil = Fil({("POST", "/api/v1/context-pack"): httpx.ReadTimeout("slow")})
    m = memory_for(fil)
    for _ in range(3):
        pack = await m.context_pack("billing", "q", [], 500)
        assert pack.memories == [] and pack.query == "q"
    assert len(fil.requetes) == 3
    await m.context_pack("billing", "q", [], 500)
    assert len(fil.requetes) == 3, "breaker open: no more waiting"
    assert (await memory_for(Fil()).context_pack("billing", "q", [], 0)).memories == [], (
        "zero budget: no call"
    )


async def test_an_http_error_on_read_never_bubbles_up() -> None:
    fil = Fil({("POST", "/api/v1/context-pack"): (500, "boom")})
    assert (await memory_for(fil).context_pack("billing", "q", [], 100)).memories == []


async def test_writing_a_fact_stores_the_choregos_notions_in_metadata() -> None:
    fil = Fil(
        {
            ("PUT", "/api/v1/memories/by-external-id"): (200, {"id": "m-42"}),
            ("POST", "/api/v1/memories"): (201, {"memory": {"id": "m-43"}, "outcome": "added"}),
        }
    )
    fact = Fact(
        kind="convention",
        subject="tests",
        content="pytest -q",
        external_id="conv-1",
        paths=["tests/"],
        provenance=Provenance(source="orchestrator", run_id="r-1"),
    )
    m = memory_for(fil)
    assert await m.write_fact("billing", fact) == "m-42"
    body = fil.corps(0)
    assert (
        body["external_id"] == "conv-1"
        and body["metadata"]["kind"] == "convention"
        and body["metadata"]["paths"] == ["tests/"]
    )
    assert "kind" not in body, "at the root, Ecphoria silently ignored it"
    assert await m.write_fact("billing", Fact(kind="run_lesson", subject="s", content="c")) == "m-43"


async def test_proposing_a_fact_puts_it_on_hold_with_its_provenance() -> None:
    fil = Fil({("POST", "/api/v1/memories"): (201, {"id": "p-1"})})
    prov = Provenance(source="agent", run_id="r-9")
    assert (
        await memory_for(fil).propose_fact("billing", Fact(kind="hotspot", subject="s", content="c"), prov)
        == "p-1"
    )
    assert fil.requetes[0].url.params["status"] == "pending"
    assert fil.corps(0)["metadata"]["provenance"]["run_id"] == "r-9"


async def test_the_search_and_the_pending_proposals_are_translated() -> None:
    fil = Fil(
        {
            ("POST", "/api/v1/memories/search"): (
                200,
                {
                    "results": [
                        {
                            "memory": {
                                "id": "m1",
                                "subject": "s",
                                "content": "c",
                                "metadata": {"kind": "incident", "provenance": {"run_id": "r-1"}},
                                "state": "expired",
                            },
                            "score": 0.4,
                        }
                    ]
                },
            ),
            ("GET", "/api/v1/pending"): (
                200,
                {"items": [{"id": "p1", "subject": "s", "content": "c", "state": "pending"}]},
            ),
            ("POST", "/api/v1/pending/p1/accept"): (200, {}),
        }
    )
    m = memory_for(fil)
    (hit,) = await m.search("billing", "q", 5)
    assert (
        hit.kind == "incident" and hit.score == 0.4 and hit.status == "rejected" and hit.proposed_by == "r-1"
    )
    (waiting,) = await m.list_pending("billing")
    assert waiting.status == "pending" and await m.accept_pending("billing", "p1") is True
    assert _memory({"id": "x"}).kind == "other"


async def test_batch_ingestion_and_the_connection_test() -> None:
    fil = Fil({("POST", "/api/v1/memories/batch"): (200, {}), ("GET", "/health"): (200, {"status": "ok"})})
    m = memory_for(fil)
    await m.ingest_events("billing", [])
    assert fil.requetes == [], "nothing to ingest, nothing sent"
    await m.ingest_events(
        "billing", [{"subject": "s", "content": "c", "kind": "decision", "external_id": "e-1"}]
    )
    assert fil.corps(0)["memories"][0]["metadata"]["provenance"] == {"source": "choregos", "ref": "e-1"}
    assert (await m.test())["ok"] is True
    assert (await memory_for(Fil({("GET", "/health"): (503, "down")})).test())["ok"] is False


async def test_named_and_global_memories_travel_in_their_own_tenant_and_namespace() -> None:
    """S40-11: a global memory is the organisation's tenant; every namespace but the project's shared one
    is sent in full in `X-Ecphoria-Namespace`."""
    fil = Fil({("POST", "/api/v1/context-pack"): (200, {})})
    m = memory_for(fil, tenant="billing")
    for namespace in (
        "billing",
        "billing#agent:architect",
        "billing#memory:decisions",
        "org:varga#memory:lessons",
    ):
        await m.context_pack(namespace, "q", [], 100)
    seen = [(r.headers["X-Ecphoria-Tenant"], r.headers.get("X-Ecphoria-Namespace")) for r in fil.requetes]
    assert seen == [
        ("billing", None),
        ("billing", "billing#agent:architect"),
        ("billing", "billing#memory:decisions"),
        ("org:varga", "org:varga#memory:lessons"),
    ]


async def test_a_namespace_that_is_none_of_the_four_forms_is_refused() -> None:
    fil = Fil({("POST", "/api/v1/context-pack"): (200, {})})
    with pytest.raises(ValueError, match="malformed"):
        await memory_for(fil).context_pack("org:varga", "q", [], 100)
