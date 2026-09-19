"""L'adaptateur mémoire contre un vrai serveur Ecphoria (S10, S11).

Ce que les fakes ne peuvent pas prouver : qu'Ecphoria accepte nos requêtes et rend ce que
le context pack promet. Le test écrit dans son propre tenant et n'en sort pas.

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

# Le serveur garde ses données entre deux exécutions : sans marqueur, la mémoire écrite au
# tour précédent ferait passer (ou échouer) le tour suivant pour de mauvaises raisons.
RUN = uuid4().hex[:8]


@pytest.fixture
async def memory() -> AsyncIterator[EcphoriaMemory]:
    import os

    env = require("CHOREGOS_LIVE_ECPHORIA_URL", "CHOREGOS_LIVE_ECPHORIA_TOKEN")
    adapter = EcphoriaMemory(
        env["CHOREGOS_LIVE_ECPHORIA_URL"],
        token=env["CHOREGOS_LIVE_ECPHORIA_TOKEN"],
        tenant=os.environ.get("CHOREGOS_LIVE_ECPHORIA_TENANT", TENANT),
        # Un serveur local répond vite, mais le timeout court reste le comportement réel :
        # on le relâche juste assez pour ne pas mesurer la latence de la machine.
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
    """Le provisioning doit pouvoir vérifier ses droits avant d'envoyer des données."""
    await memory.create_tenant(TENANT)


async def test_ecrire_puis_retrouver_un_fait(memory: EcphoriaMemory) -> None:
    written = await memory.write_fact(
        TENANT,
        fact("convention:billing:devise", "les montants sont en centimes, jamais en flottants"),
    )
    assert written, "l'écriture rend un identifiant"

    found = await memory.search(TENANT, "centimes flottants", k=5)
    assert any("centimes" in item.content for item in found), found
    retrouve = next(item for item in found if "centimes" in item.content)
    assert str(retrouve.kind) == "convention", "le type survit à l'aller-retour"
    assert retrouve.provenance.get("source") == "orchestrator"


async def test_un_external_id_rejoue_ne_duplique_pas(memory: EcphoriaMemory) -> None:
    proposition = fact(
        "ticket:varga/billing-api#7",
        "le ticket #7 a été livré sans régression",
        external_id="varga/billing-api#7",
    )
    first = await memory.write_fact(TENANT, proposition)
    second = await memory.write_fact(TENANT, proposition)
    assert first == second, "une redélivrance confirme, elle ne crée pas un second souvenir"


async def test_le_context_pack_respecte_son_budget_et_les_chemins(memory: EcphoriaMemory) -> None:
    await memory.write_fact(
        TENANT,
        fact(
            "convention:orders:arrondi",
            "l'arrondi se fait au centime le plus proche, moitié vers le haut " + "x" * 600,
            paths=["src/orders/**"],
        ),
    )
    await memory.write_fact(
        TENANT,
        fact(
            "incident:2026-03-02",
            "incident du 2 mars : double facturation sur les avoirs",
            kind="incident",
            paths=["src/billing/**"],
        ),
    )

    pack = await memory.context_pack(
        TENANT, "corriger l'arrondi des commandes", paths=["src/orders/total.py"], budget_tokens=4000
    )
    assert pack.memories, "le pack rend la convention du périmètre demandé"
    assert all("billing" not in m.subject for m in pack.memories), "hors périmètre, hors pack"
    assert pack.tokens_estimated <= 4000

    serre = await memory.context_pack(TENANT, "arrondi", paths=[], budget_tokens=20)
    assert serre.tokens_estimated <= 20, "le budget est un plafond"
    assert serre.truncated is True


async def test_le_pack_distingue_les_incidents_des_conventions(memory: EcphoriaMemory) -> None:
    pack = await memory.context_pack(TENANT, "facturation avoirs", paths=[], budget_tokens=4000)
    assert pack.incidents, "un incident n'est pas une convention"
    assert all(str(item.kind) == "incident" for item in pack.incidents)


async def test_un_agent_propose_un_humain_valide(memory: EcphoriaMemory) -> None:
    proposed = await memory.propose_fact(
        TENANT,
        fact(f"convention:tests:couverture:{RUN}", f"la couverture {RUN} ne doit pas descendre sous 80 %"),
        Provenance(source="agent", run_id="run-live-1"),
    )
    assert proposed

    # Tant que personne n'a tranché, la proposition n'est pas une vérité.
    found = await memory.search(TENANT, f"couverture {RUN}", k=10)
    assert all(RUN not in item.content for item in found), found

    pending = await memory.list_pending(TENANT)
    assert any(item.id == proposed for item in pending), pending
    propose_item = next(item for item in pending if item.id == proposed)
    assert propose_item.status == "pending"
    assert propose_item.proposed_by == "run-live-1", "on sait quel run l'a proposée"

    assert await memory.accept_pending(TENANT, proposed) is True
    apres = await memory.search(TENANT, f"couverture {RUN}", k=10)
    assert any(RUN in item.content for item in apres), apres


async def test_une_proposition_refusee_ne_revient_pas(memory: EcphoriaMemory) -> None:
    proposed = await memory.propose_fact(
        TENANT,
        fact(f"convention:prod:cache:{RUN}", f"on peut vider le cache de prod {RUN} à chaud"),
        Provenance(source="agent", run_id="run-live-2"),
    )
    assert await memory.reject_pending(TENANT, proposed) is True

    # La recherche lexicale ramène aussi ce que les autres tests ont écrit : on cherche la
    # phrase refusée, pas le marqueur de run.
    found = await memory.search(TENANT, f"vider le cache de prod {RUN}", k=10)
    assert all("vider le cache" not in item.content for item in found), found
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
                "content": f"règle importée numéro {index}",
            }
            for index in range(5)
        ],
    )
    found = await memory.search(TENANT, "règle importée", k=10)
    assert len(found) >= 1, found


async def test_une_memoire_injoignable_rend_un_pack_vide_pas_une_erreur() -> None:
    """La règle de S10 : une mémoire lente ou morte ne bloque jamais une étape."""
    mort = EcphoriaMemory("http://127.0.0.1:9", token="x", tenant=TENANT, read_timeout_ms=50)
    try:
        pack = await mort.context_pack(TENANT, "peu importe", paths=[], budget_tokens=1000)
        assert pack.memories == [] and pack.incidents == []
        assert pack.tokens_estimated == 0
    finally:
        await mort.aclose()
