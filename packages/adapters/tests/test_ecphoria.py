"""Ecphoria : lecture à timeout court qui ne bloque jamais, écriture gouvernée, disjoncteur."""

from __future__ import annotations

import httpx
from choregos_adapters.memory.ecphoria import EcphoriaMemory, _memory
from choregos_core.domain import Fact, Provenance

from ._transport import Fil

URL = "http://ecphoria.test"


def memoire(fil: Fil, **kw: object) -> EcphoriaMemory:
    return EcphoriaMemory(URL, token="tok", client=fil.client(), **kw)  # type: ignore[arg-type]


async def test_le_context_pack_porte_le_tenant_et_traduit_la_reponse() -> None:
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
    pack = await memoire(fil).context_pack("billing", "avoirs", ["src/"], 2000, kinds=["convention"])
    assert pack.tokens_estimated == 120 and pack.truncated and pack.memories[0].id == "m1"
    assert pack.related_items[0].key == "K-1"
    assert fil.requetes[0].headers["X-Ecphoria-Tenant"] == "billing"
    assert fil.requetes[0].headers["Authorization"] == "Bearer tok"
    assert fil.corps(0)["budget_tokens"] == 2000 and fil.corps(0)["kinds"] == ["convention"]


async def test_une_panne_rend_un_pack_vide_et_trois_pannes_ouvrent_le_disjoncteur() -> None:
    fil = Fil({("POST", "/api/v1/context-pack"): httpx.ReadTimeout("lent")})
    m = memoire(fil)
    for _ in range(3):
        pack = await m.context_pack("billing", "q", [], 500)
        assert pack.memories == [] and pack.query == "q"
    assert len(fil.requetes) == 3
    await m.context_pack("billing", "q", [], 500)
    assert len(fil.requetes) == 3, "disjoncteur ouvert : on n'attend plus"
    assert (await memoire(Fil()).context_pack("billing", "q", [], 0)).memories == [], (
        "budget nul : pas d'appel"
    )


async def test_une_erreur_http_de_lecture_ne_remonte_jamais() -> None:
    fil = Fil({("POST", "/api/v1/context-pack"): (500, "boom")})
    assert (await memoire(fil).context_pack("billing", "q", [], 100)).memories == []


async def test_ecrire_un_fait_range_les_notions_choregos_dans_metadata() -> None:
    fil = Fil(
        {
            ("PUT", "/api/v1/memories/by-external-id"): (200, {"id": "m-42"}),
            ("POST", "/api/v1/memories"): (201, {"memory": {"id": "m-43"}, "outcome": "added"}),
        }
    )
    fait = Fact(
        kind="convention",
        subject="tests",
        content="pytest -q",
        external_id="conv-1",
        paths=["tests/"],
        provenance=Provenance(source="orchestrator", run_id="r-1"),
    )
    m = memoire(fil)
    assert await m.write_fact("billing", fait) == "m-42"
    corps = fil.corps(0)
    assert (
        corps["external_id"] == "conv-1"
        and corps["metadata"]["kind"] == "convention"
        and corps["metadata"]["paths"] == ["tests/"]
    )
    assert "kind" not in corps, "à la racine, Ecphoria l'ignorait en silence"
    assert await m.write_fact("billing", Fact(kind="run_lesson", subject="s", content="c")) == "m-43"


async def test_proposer_un_fait_le_met_en_attente_avec_sa_provenance() -> None:
    fil = Fil({("POST", "/api/v1/memories"): (201, {"id": "p-1"})})
    prov = Provenance(source="agent", run_id="r-9")
    assert (
        await memoire(fil).propose_fact("billing", Fact(kind="hotspot", subject="s", content="c"), prov)
        == "p-1"
    )
    assert fil.requetes[0].url.params["status"] == "pending"
    assert fil.corps(0)["metadata"]["provenance"]["run_id"] == "r-9"


async def test_la_recherche_et_les_propositions_en_attente_se_traduisent() -> None:
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
    m = memoire(fil)
    (hit,) = await m.search("billing", "q", 5)
    assert (
        hit.kind == "incident" and hit.score == 0.4 and hit.status == "rejected" and hit.proposed_by == "r-1"
    )
    (attente,) = await m.list_pending("billing")
    assert attente.status == "pending" and await m.accept_pending("billing", "p1") is True
    assert _memory({"id": "x"}).kind == "other"


async def test_l_ingestion_par_lot_et_le_test_de_connexion() -> None:
    fil = Fil({("POST", "/api/v1/memories/batch"): (200, {}), ("GET", "/health"): (200, {"status": "ok"})})
    m = memoire(fil)
    await m.ingest_events("billing", [])
    assert fil.requetes == [], "rien à ingérer, rien envoyé"
    await m.ingest_events(
        "billing", [{"subject": "s", "content": "c", "kind": "decision", "external_id": "e-1"}]
    )
    assert fil.corps(0)["memories"][0]["metadata"]["provenance"] == {"source": "choregos", "ref": "e-1"}
    assert (await m.test())["ok"] is True
    assert (await memoire(Fil({("GET", "/health"): (503, "down")})).test())["ok"] is False
