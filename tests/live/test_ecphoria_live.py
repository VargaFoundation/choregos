"""The memory adapter against a real Ecphoria server (S10, S11).

What the fakes cannot prove: that Ecphoria accepts our requests and returns what the context
pack promises. The test writes into its own tenant and never leaves it.

    CHOREGOS_LIVE_ECPHORIA_URL=http://127.0.0.1:8432 CHOREGOS_LIVE_ECPHORIA_TOKEN=… \
    uv run pytest tests/live -m live
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from choregos_adapters.memory.ecphoria import EcphoriaMemory
from choregos_core.domain import Fact, Provenance

from .conftest import require

pytestmark = pytest.mark.live

TENANT = "choregos-live"

# The server keeps its data between two runs: without a marker, the memory written on the
# previous round would make the next round pass (or fail) for the wrong reasons.
RUN = uuid4().hex[:8]


@pytest.fixture
async def memory() -> AsyncIterator[EcphoriaMemory]:
    import os

    env = require("CHOREGOS_LIVE_ECPHORIA_URL", "CHOREGOS_LIVE_ECPHORIA_TOKEN")
    adapter = EcphoriaMemory(
        env["CHOREGOS_LIVE_ECPHORIA_URL"],
        token=env["CHOREGOS_LIVE_ECPHORIA_TOKEN"],
        tenant=os.environ.get("CHOREGOS_LIVE_ECPHORIA_TENANT", TENANT),
        # A local server answers fast, but the short timeout is still the real behaviour: it is
        # relaxed just enough not to measure the machine's latency.
        read_timeout_ms=2000,
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


def fact(subject: str, content: str, **kwargs: object) -> Fact:
    return Fact(
        kind=kwargs.pop("kind", "convention"),  # type: ignore[arg-type]
        subject=subject,
        content=content,
        provenance=Provenance(source="orchestrator", ref="tests/live"),
        **kwargs,  # type: ignore[arg-type]
    )


async def test_le_serveur_repond(memory: EcphoriaMemory) -> None:
    assert (await memory.test())["ok"] is True


async def test_le_tenant_est_cree_avant_la_premiere_ecriture(memory: EcphoriaMemory) -> None:
    """Provisioning must be able to check its rights before sending data."""
    await memory.create_tenant(TENANT)


async def test_ecrire_puis_retrouver_un_fait(memory: EcphoriaMemory) -> None:
    written = await memory.write_fact(
        TENANT,
        fact("convention:billing:devise", "amounts are in cents, never in floats"),
    )
    assert written, "the write returns an identifier"

    found = await memory.search(TENANT, "cents floats", k=5)
    assert any("cents" in item.content for item in found), found
    retrouve = next(item for item in found if "cents" in item.content)
    assert str(retrouve.kind) == "convention", "the kind survives the round trip"
    assert retrouve.provenance.get("source") == "orchestrator"


async def test_un_external_id_rejoue_ne_duplique_pas(memory: EcphoriaMemory) -> None:
    proposition = fact(
        "ticket:varga/billing-api#7",
        "ticket #7 was delivered without regression",
        external_id="varga/billing-api#7",
    )
    first = await memory.write_fact(TENANT, proposition)
    second = await memory.write_fact(TENANT, proposition)
    assert first == second, "a redelivery confirms, it does not create a second memory"


async def test_le_context_pack_respecte_son_budget_et_les_chemins(memory: EcphoriaMemory) -> None:
    await memory.write_fact(
        TENANT,
        fact(
            "convention:orders:arrondi",
            "rounding goes to the nearest cent, halves rounded up " + "x" * 600,
            paths=["src/orders/**"],
        ),
    )
    await memory.write_fact(
        TENANT,
        fact(
            "incident:2026-03-02",
            "incident of 2 March: double billing on credit notes",
            kind="incident",
            paths=["src/billing/**"],
        ),
    )

    pack = await memory.context_pack(
        TENANT, "fix the rounding of orders", paths=["src/orders/total.py"], budget_tokens=4000
    )
    assert pack.memories, "the pack returns the convention of the requested scope"
    assert all("billing" not in m.subject for m in pack.memories), "out of scope, out of the pack"
    assert pack.tokens_estimated <= 4000

    serre = await memory.context_pack(TENANT, "rounding", paths=[], budget_tokens=20)
    assert serre.tokens_estimated <= 20, "the budget is a ceiling"
    assert serre.truncated is True


async def test_le_pack_distingue_les_incidents_des_conventions(memory: EcphoriaMemory) -> None:
    pack = await memory.context_pack(TENANT, "billing credit notes", paths=[], budget_tokens=4000)
    assert pack.incidents, "an incident is not a convention"
    assert all(str(item.kind) == "incident" for item in pack.incidents)


async def test_un_agent_propose_un_humain_valide(memory: EcphoriaMemory) -> None:
    proposed = await memory.propose_fact(
        TENANT,
        fact(f"convention:tests:coverage:{RUN}", f"coverage {RUN} must not drop below 80 %"),
        Provenance(source="agent", run_id="run-live-1"),
    )
    assert proposed

    # As long as nobody has ruled, the proposal is not a truth.
    found = await memory.search(TENANT, f"coverage {RUN}", k=10)
    assert all(RUN not in item.content for item in found), found

    pending = await memory.list_pending(TENANT)
    assert any(item.id == proposed for item in pending), pending
    propose_item = next(item for item in pending if item.id == proposed)
    assert propose_item.status == "pending"
    assert propose_item.proposed_by == "run-live-1", "we know which run proposed it"

    assert await memory.accept_pending(TENANT, proposed) is True
    apres = await memory.search(TENANT, f"coverage {RUN}", k=10)
    assert any(RUN in item.content for item in apres), apres


async def test_une_proposition_refusee_ne_revient_pas(memory: EcphoriaMemory) -> None:
    proposed = await memory.propose_fact(
        TENANT,
        fact(f"convention:prod:cache:{RUN}", f"the prod cache {RUN} can be flushed while hot"),
        Provenance(source="agent", run_id="run-live-2"),
    )
    assert await memory.reject_pending(TENANT, proposed) is True

    # The lexical search also brings back what the other tests wrote: we look for the rejected
    # sentence, not the run marker.
    found = await memory.search(TENANT, f"flushed prod cache {RUN}", k=10)
    assert all("flushed while hot" not in item.content for item in found), found
    assert all(item.id != proposed for item in await memory.list_pending(TENANT))


async def test_l_ingestion_par_lot_passe(memory: EcphoriaMemory) -> None:
    await memory.ingest_events(
        TENANT,
        [
            {
                "external_id": f"doc:{index}",
                "source": "provisioning",
                "kind": "convention",
                "subject": f"convention:doc:{index}",
                "content": f"imported rule number {index}",
            }
            for index in range(5)
        ],
    )
    found = await memory.search(TENANT, "imported rule", k=10)
    assert len(found) >= 1, found


async def test_une_memoire_injoignable_rend_un_pack_vide_pas_une_erreur() -> None:
    """The S10 rule: a slow or dead memory never blocks a stage."""
    mort = EcphoriaMemory("http://127.0.0.1:9", token="x", tenant=TENANT, read_timeout_ms=50)
    try:
        pack = await mort.context_pack(TENANT, "never mind", paths=[], budget_tokens=1000)
        assert pack.memories == [] and pack.incidents == []
        assert pack.tokens_estimated == 0
    finally:
        await mort.aclose()
