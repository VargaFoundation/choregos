"""Les deux connecteurs « il n'y en a pas » : ce qu'ils rendent, et ce qu'ils avouent."""

from __future__ import annotations

from choregos_adapters.gateway.direct import DirectGateway
from choregos_adapters.tracker.interne import InternalTracker
from choregos_contracts import ProjectConfig
from choregos_core.domain import NewItem, TrackerStateMapping


async def test_la_passerelle_directe_rend_une_cle_unique_par_run() -> None:
    """`key_id` porte un index unique : un identifiant constant faisait échouer le second run."""
    gateway = DirectGateway("sk-perso", models=["claude"])
    a = await gateway.mint_key({"run_id": "r-1"}, budget_usd=5.0, ttl_s=60, models=["m"])
    b = await gateway.mint_key({"run_id": "r-2"}, budget_usd=5.0, ttl_s=60, models=["m"])
    assert a.key == b.key == "sk-perso"
    assert a.key_id == "direct:r-1" and b.key_id == "direct:r-2"
    sans_run = await gateway.mint_key({}, 1.0, 60, [])
    assert sans_run.key_id.startswith("direct:") and sans_run.key_id != "direct:"


async def test_la_passerelle_directe_ne_mesure_rien_et_le_dit_par_un_zero() -> None:
    gateway = DirectGateway()
    depense = await gateway.spend("direct:r-1")
    assert depense.cost_usd == 0 and depense.tokens_in == 0
    assert await gateway.revoke("direct:r-1") is None
    assert [m.model_name for m in await DirectGateway(models=["a", "b"]).list_models()] == ["a", "b"]


async def test_le_tracker_interne_ne_possede_pas_les_tickets() -> None:
    """`create_item` rend une clé VIDE : c'est la plateforme qui nomme — rendre le titre
    fabriquait des clés illisibles et non uniques (banc du 2026-09-24)."""
    tracker = InternalTracker()
    assert tracker.owns_items is False
    assert await tracker.create_item(NewItem(title="Un titre", body="")) == ""
    item = await tracker.fetch_item("RH-1")
    assert item.key == "RH-1" and item.title == "RH-1"
    assert await tracker.list_candidates(ProjectConfig(slug="p", org="o")) == []


async def test_le_tracker_interne_n_accepte_aucun_webhook() -> None:
    tracker = InternalTracker()
    assert tracker.verify_webhook({"X-Anything": "x"}, b"{}") is False
    assert tracker.parse_webhook({}, b"{}") == []
    assert await tracker.set_state("RH-1", TrackerStateMapping(label="x")) is None
    assert await tracker.comment("RH-1", "texte") == ""
